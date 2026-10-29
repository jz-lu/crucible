"""Supplemental mathematical evidence for frozen HQC v3 configurations.

This module does not change the frozen manifest, profile identity or algorithms.
The deterministic result is conditional on faithful implementations of cyclic
binary multiplication, truncation, nearest-word RM decoding and bounded-distance
RS decoding. It needs no IID assumption, sampler idealization, or rare-tail model.
No import-time file reading or computation is performed.
"""

MODEL_ID = 'hqc-20261012-model1-ties-bias-v1'
UPSTREAM_COMMIT = '5a1a854b0dc9f2141bdc771c555ee60c37950183'
SOURCE_PINS = {
    'upstream_archive_sha256': '83c6e6cff490638312e1e20ed5de593fb5d7bfab162235f6238f519d6a1feafb',
    'reed_muller.c': 'aca120a4c67e7d3653972d86130fec2aba490c3a971c2c085d55aa2a20662da6',
    'reed_solomon.c': '9e01d64d7b91dd5f8a91a9a9ead64d24cfb9240c94929be9cdd2fcf232b4551c',
    'specification_20261012_pdf': '009703472472887eed33551df2dab5dfd8c0bd1a98ec944068b6a5679de5a9d5',
}


def support_bounds(row):
    """Compute support evidence only for the reviewed concatenated codec domain.

    Each RM word has distance n2/2 and therefore corrects fewer than n2/4
    bit errors. If total weight <(delta+1)*n2/4, at most delta RM words can
    be incorrect. RS(n1,16) corrects delta=(n1-16)/2 arbitrary symbol errors.
    The cyclic residual has weight <=2*w*wr+we before truncation.
    """
    fields=('n','weight','encryption_weight','error_weight','rs_length','rm_length',
            'rs_delta','rm_repetitions','code_length')
    if any(type(row.get(k)) is not int for k in fields):
        raise ValueError('Expected exact integer HQC parameters')
    n,w,wr,we,n1,n2,delta,repetitions,length=(row[k] for k in fields)
    if (not 1<=w<=n or not 1<=wr<=n or not 1<=we<=n
        or repetitions not in (1,2,3) or n2!=128*repetitions
        or delta not in range(1,16) or n1!=16+2*delta or length!=n1*n2 or n<=length):
        raise ValueError('Outside the reviewed RS(.,16)/repeated RM(1,7) domain')
    residual=2*w*wr+we
    failure_minimum=(delta+1)*(n2//4)
    perfect=residual<failure_minimum
    return {'parameters':{k:row[k] for k in fields},
            'residual_weight_maximum':residual,
            'inner_decoding_failure_weight_minimum':n2//4,
            'outer_symbol_error_capacity':delta,
            'decoder_error_capacity':failure_minimum-1,
            'deterministic_guarantee_established':perfect}


def supplemental_evidence(row):
    """Attach tail labels only to exact reviewed parameters/certificate bindings."""
    bounds=support_bounds(row)
    perfect=bounds['deterministic_guarantee_established']
    result={**bounds,
        'kind':'deterministic-support' if perfect else 'conditional-tail-model',
        'label':'Deterministic decoding guarantee' if perfect else 'Conditional model bound',
        'scope':'Honest supported inputs; faithful arithmetic, encodings and decoder implementation; resource aborts separate.',
        'requires_independent_noise_surrogate':not perfect,
        'replaces_frozen_certificate':False,
    }
    if perfect:
        result['failure_bound']='0'
        result['model_transfer_required']=False
    else:
        import hashlib, json
        cert=row.get('correctness_certificate',{})
        binding=json.dumps({'parameters':bounds['parameters'],'certificate':cert},sort_keys=True,separators=(',',':'))
        if hashlib.sha256(binding.encode()).hexdigest() not in REVIEWED_TAIL_BINDINGS:
            raise ValueError('Tail certificate is not bound to a reviewed parameter tuple')
        if (cert.get('model_id')!=MODEL_ID or cert.get('meets_target') is not True
            or type(cert.get('certified_model_bound_exponent')) is not int
            or cert['certified_model_bound_exponent']<128):
            raise ValueError('No recognized conditional-tail certificate supplied')
        result['failure_bound']='2^-'+str(cert['certified_model_bound_exponent'])
        result['model_id']=MODEL_ID
        result['model_transfer_required']=True
        result['qualification']='Exact arithmetic inside the model; transfer to actual correlated cyclic noise remains conjectural.'
    return result


# Canonical hashes of {parameters, certificate}, derived from the pinned table.
# Changing parameters or any certificate field cannot reuse a reviewed tail label.
REVIEWED_TAIL_BINDINGS = frozenset((
    '99bfe8c81a4c87605852c78371ecb9cf66a93909e4fa898a8d6078641e135bd5',
    'ed4a22dd39afa528aded52a702189e2d6a69807780f3dfa63cd0243f0835410d',
    '7364797b6153bb2de03c8fcfce4fffea6bda6cd97287926f3caed1f97fe24bd6',
    '07a5d806c723042b59fcd322e57ac28f4fe08a701bcf1fa494c930a15689382b',
    'fb377ce543c5c8db9945420115cca7055b51298a377e3f3eddc406bfcf60c012',
    '03760f0ca72c3a30f3518e64bb26aed2bc764047b4d031efb6aba400cc26d6cf',
    '4c36761e5b0b1b6c94c64f6e96b149a66b054eae336db8fa91b0467bdd77c996',
    'c0359db413f5b5e3c32b2e076f3896a8c02365f57e6bb78fdf7f7572298997c3',
    'a3d22a6709a553121054c122b3b35bf7038250c279c4e4599e9234ee28223e49',
    '5d8c58fb02511ea90a02789d247edde57e12dd0102d9e2294e37d222b1cb8fd3',
    '845ee05a7077d905662a171bd42343e8668a03f1aa487e93feffd84b9288ccfe',
    '07556cf87711fadaf6f6606be51f7e3388d81cd67d75af1b61e01b1e71337668',
    'ae66eae5d740a3dda67d4768f5baa92f1c20c8c6be97bc7d35d4790f37cb1ddd',
    '1ac26e078cf5589af9644a1b836f14a76200077409b43fe277abcbf5a5163156',
    '8563aa47ae7a5fe9de30ce6f981562f48748150cc2ca3a8b08557f076a09e40b',
    '799c01d71b1a02e20fb3cd26ba462b6662e52477d112062a3fd6f4a07c4c4f1d',
    'a5812194d77e82011dfa716433814e731f803ec8db4c4611206e14b03dd8b06a',
    '6512713259802cefbdb516dcbf7b78ff82162878be11a3e842d3d76e159efb2a',
    '4e906cae7eab1474135ba4524a6d7d5c0239c55d4510234e97c4c048c6ea82c4',
    'aaaedc6c628aa38b440e218d93087af121879114cdc65a5e464271bf194d139c',
    '242d3d8b06b6411481b03de97b1f4d38fcce3762c14cf8c8b7b6ab259bd24db4',
    'eb623cf4f485051ded16814278d57ad56612e11cdb207fffb11bd632270f37b2',
    'd3ed8cda7ba843cdf32a470e21137dc425069ee17509a5899e9bcfd6b48f0fc3',
    '0a885b079d46a6b79cc96d13dd2e3b656dced4746ec084fb551e631bdbb1874c',
    'b66752756fa0463da47b57b8d868d5e73554e7f04af9bc25ea7a22722a75bd19',
    'ed5b9507ffcf61f590d09dd9cb319ecc84443d1482737d75ead7f7799079c273',
    '33d7508c72553c9c90d0ff71178e48df413094460de7643f1866766cb5d27b78',
    '31766cd86cc674e21e40037d6df1133b7eac70425c75a885e9d776cb8d83d597',
    'c3eaf15167363c5bd9804444b86da41e118d4e167c2736606734c926aa800b4c',
    'ee77a77a800f2ca61f3828ce35812798be5b42831d8d3b946b14230b6f693e54',
    '0d563f99fad150880c103aeffe199cd2b6f52e2688cc88ca9446262b5cbc880f',
    'a53a3b113514f0bc9616cb8c76b84881fcc3cb82dd4d5d519ae66eeaeeafcda4',
    '4db963d4336fafc50fe5fd69b6223b2f989805f9c664ee256b2649b9d50b36f8',
    'fdc7bb169baeb616042b026274139975479997dde207d781bf8602307071925e',
    '8cfcc3f03f4f7b9c8555a79d6c2cd74fe92293a6b9b42ab64fcdf27ce4414026',
    '2a2015d2f88a590eb40add6a70210c07b198fde5c6c529a01556b0a793cabd4a',
    '267dbce57698ac74fa37fe9c185452d5cdba3c9261d0c6cdae4fca18c28bfce5',
    '40e7a5d548d1c02cb3cb23c5896c46d44719fd484017bec28408d54cfba4ba24',
    'a47a83e9c61cc2044d92c8f8f1273e319f9e0903861c6720d652bf7da459df81',
    '81f2fcb2e355ba1dd5e24601ee7e404324c88daa05eb0348d6c3c3cf018d155d',
    'b0e14c983acb37c3c54b96bd8a286b9783a0f82c89ea8e57214989f7ea37547f',
    '223a5e27a422ebd1fd7ff2e3e6464e4b445d8271aca7fd616feb547359999bd3',
    '695b66305fd4bdf434b858dd7b204ca97dbd001d9e00aa809282042715539b10',
    '053aa45311fa0db90c37a4caff3112a7388917522fe83eb4971df7dfbafbafd7',
    '4aa18cd812605b7e105527d26e372d9a6f81f0b9052830f7ff89d589a9d33b85',
    'd096f036539cca5be1f0fba8c9b3552eb593b357382da815aa388bd8785f101b',
    '1ebedbb3328422c968a23a5c5211935f5fec716a150f9a5d749b2c709c860f2c',
    '1d2646b6c031c9cfbf37f490ab4d5747c813bf6c564de547e8713e610040aae1',
    '98b72a6758f9de6526b1d8d07ea0f6f39266d2090511e526b457c24ea8cb4d3f',
    '861a73e69fa7745c53a900cc7d9e52bae79aa05cee103044430beda05de8b9aa',
    '9502ab8a5963d4f02401e381f183b68f62d0f6bbb7b4f628a2f2fd6496de8333',
    '9b08d9f06e946111a4205d9fc52be7ce2d53ea84ca213b5947129eec7a632d96',
    'c323db9073df60c926103f010d968fe2221acf73508f50190e0f50fb20060608',
    'e9287760fb32c7b874a80fad228b50b6fbd87ce3e7e158119498a1442b429411',
    'c021c0f20a0137f97deb06cec490bc04ae22fa7c57ffe03165de8bfc4f09b7ce',
    '50df9c1ca288d0bb7abd5dcaa002f826132640c1ac3555cf7490799add264ef8',
    'cede3a0c593e2f783874b977088957eb62adeb5c8208cac35309acde393aea2e',
    '071c05a89acafa769f4b9c8d02b686e2ae686671c1f92fe46d8216b54fe523d3',
    '9cd3fb73819296a630f96c3002e42270dc990f46c55212667ca95f539eff866a',
    '891bb8f254488aabd651eaf3b9c70271ee76d7440e3f89ca3fd4f21ce2928e72',
    '170ced7722985d196507ef7a2c34c59a8af5869b5ead4847bc6edca21b60642b',
    '013faae5579a1f1fdba6b1c9782519ee68ab1b994465f70609b862ab450dcc30',
))

PARAMETERS_SHA256 = 'f3b203d83894bbcdf8186d66119714c30caf7fd7500150560d44f2984fd36ff2'

def load_reviewed_profiles(path):
    """Authenticate the frozen input before attaching model-certificate labels."""
    import hashlib, json
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PARAMETERS_SHA256:
        raise ValueError('This supplemental review covers only the frozen HQC v3 table')
    return json.loads(raw)['profiles']

def main():
    import argparse, json
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parameters',type=Path,default=root/'crucible/data/hqc_model_profiles.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    rows=load_reviewed_profiles(args.parameters)
    evidence=[{'stage':row['tier'],**supplemental_evidence(row)} for row in rows]
    args.output.write_text(json.dumps({'schema':'crucible-hqc-supplemental-support-v1',
        'parameters_sha256':PARAMETERS_SHA256,'source_commit':UPSTREAM_COMMIT,
        'source_pins':SOURCE_PINS,'profiles':evidence},indent=2)+'\n')

if __name__=='__main__': main()
