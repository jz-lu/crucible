# Independent exact algebra/admissibility checks, not runtime generation.
import json
from pathlib import Path
R=PolynomialRing(GF(2),'x');x=R.gen()
field_polynomial=x^12+x^3+1
assert field_polynomial.is_irreducible()
F=GF(2^12,name='a',modulus=field_polynomial);a=F.gen()
S=PolynomialRing(F,'y');y=S.gen()
assert (y^64+y^3+y+a).is_irreducible()
assert ZZ(17669).is_prime(proof=True)
assert Mod(2,17669).multiplicative_order()==17668
# Actual duplicated RM(1,7): enumerate every nonzero codeword, not noise draws.
weights=[]
for symbol in range(1,256):
    weight=sum(((symbol>>7)+sum(((symbol>>j)&1)*((i>>j)&1) for j in range(7)))%2 for i in range(128))
    weights.append(3*weight)
assert min(weights)==192
# Original GF(256) and RS generator roots alpha^1..alpha^30.
Q=GF(2^8,name='b',modulus=x^8+x^4+x^3+x^2+1);b=Q.gen()
assert b.multiplicative_order()==255
T=PolynomialRing(Q,'z');z=T.gen()
g=prod(z+b^i for i in range(1,31))
expected=[89,69,153,116,176,117,111,75,73,233,242,233,65,210,21,139,103,173,67,118,105,210,174,110,74,69,228,82,255,181,1]
assert [int(c.to_integer()) for c in g.list()]==expected
assert g.degree()==30
assert all(2*w*w+w < 16*96 for w in range(1,28))
print('Verified exact fields, primitive HQC ring prime, RM distance192, RS generator and27 decoder bounds')
