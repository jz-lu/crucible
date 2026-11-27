/* Based on feature/quick-tour (4f596b8): optional, read-only workspace orientation.
   Application actions stay in the real pages; presentation shares the live guides. */
(() => {
  'use strict';
  const routes = new Set(['#challenge','#notebook','#bounties','#activity?kind=notes']);
  let sequence = 0;
  const node = (tag,className,text) => {
    const element = document.createElement(tag); element.className = className;
    if (text !== undefined) element.textContent = text; return element;
  };
  function featureIcon(kind) {
    const shared = document.querySelector(kind === 'bounties' ? '#home-bounties-title svg' : kind === 'schemes' ? '#home-weekly-title svg' : '.no-shared-feature-icon');
    if (shared?.cloneNode) { const svg = shared.cloneNode(true); svg.setAttribute('class','orientation-feature-icon'); svg.setAttribute('data-icon',kind); return svg; }
    const paths = {
      schemes:'M3 7l9-5 9 5-9 5-9-5Zm0 5 9 5 9-5M3 17l9 5 9-5',
      bounties:'m8 3 2 4h4l2-4M9 8c-3 3-6 5-6 9 0 3 3 5 9 5s9-2 9-5c0-4-3-6-6-9M12 12v6m-2-5h3a1.5 1.5 0 0 1 0 3h-2a1.5 1.5 0 0 0 0 3h3',
      notes:'M15 2H5v20h14V6l-4-4v5h4M8 11h8M8 15h8M8 19h5',
      activity:'M12 4a8 8 0 1 1-8 8M4 4v5h5M12 7v5l4 2',
      people:'M4 21v-7h4v7M10 21V4h4v17M16 21V9h4v12',
      proposal:'m4 16 11-11 4 4L8 20l-5 1 1-5ZM13 7l4 4M18 1v3M21 4h3',
      points:'m12 2 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z',
      about:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20M12 10v7M12 6v1'
    };
    if (!document.createElementNS) return node('span','orientation-feature-icon','↗');
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg'), path = document.createElementNS('http://www.w3.org/2000/svg','path');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('data-icon',kind); svg.setAttribute('class','orientation-feature-icon'); svg.setAttribute('aria-hidden','true'); path.setAttribute('d',paths[kind] || paths.schemes); svg.append(path); return svg;
  }
  function create({launcher,context,navigate,steps,presentation,overview,actions = {},isHome = () => false,allowedRoute = route => routes.has(route),getStarted,onStart = () => {},onStop = () => {}}) {
    let active = false, minimized = false, identity = null, itinerary = [], index = 0, expectedRoute = null;
    let dialog, card, overlay, progress, title, body, back, next, hide, close, directory, begin, points;
    let mode = 'tour';
    let observer = null, deadline = null, target = null, revealEpoch = 0;
    function visible(element) {
      if (!element?.isConnected || element.closest?.('[hidden]') || element.getClientRects && !element.getClientRects().length) return false;
      const style = window.getComputedStyle?.(element);
      return !style || !['hidden','collapse'].includes(style.visibility);
    }
    function clearTargetWatch() {
      revealEpoch++;
      observer?.disconnect(); observer = null;
      if (deadline !== null) window.clearTimeout(deadline); deadline = null;
      target?.classList.remove('orientation-target'); target = null;
    }
    function restoreFocus() {
      const destination = visible(launcher) ? launcher : Array.from(document.querySelectorAll('h1,h2')).find(visible);
      if (!destination) return;
      const added = !destination.hasAttribute?.('tabindex') && destination !== launcher;
      if (added) destination.setAttribute('tabindex','-1'); destination.focus({preventScroll:true});
      if (added) destination.removeAttribute('tabindex');
    }
    function stop(restore = true, reason = 'close') {
      if (!active) return;
      active = false; clearTargetWatch(); document.removeEventListener?.('click',outsideClick,true);
      window.removeEventListener('hashchange',checkContext); window.removeEventListener('popstate',checkContext);
      window.removeEventListener('resize',place); window.removeEventListener('scroll',place,true); window.removeEventListener('pagehide',pagehide);
      if (dialog.open) dialog.close(); card.remove(); overlay.remove(); dialog.remove();
      identity = null; itinerary = []; expectedRoute = null; minimized = false;
      onStop(reason); if (restore) restoreFocus();
    }
    function outsideClick(event) {
      if (!active || minimized || event.target.closest?.('.orientation-card') === card || card.contains?.(event.target) || target?.contains?.(event.target)) return;
      event.preventDefault(); event.stopImmediatePropagation?.(); event.stopPropagation?.(); setMinimized(true);
    }
    function cancel() { stop(); }
    function pagehide() { stop(false,'pagehide'); }
    function checkContext() {
      if (!active) return false;
      if (context() !== identity) { stop(false,'context'); return false; }
      if (!minimized && window.location.hash !== expectedRoute) { stop(true,'navigation'); return false; }
      return true;
    }
    function place() {
      if (!checkContext()) return;
      if (mode === 'overview' && !minimized) { overlay.setAttribute('hidden',''); return; }
      presentation.place({panel:card,overlay,target,minimized});
    }
    function validSteps(proposed) {
      if (!Array.isArray(proposed) || !proposed.length || proposed.some(step => !allowedRoute(step?.route) || typeof step.target !== 'string' || !step.target || typeof step.title !== 'string' || typeof step.body !== 'string' || step.fallback !== undefined && typeof step.fallback !== 'string')) return false;
      try { for (const step of proposed) { document.querySelector(step.target); if (step.fallback) document.querySelector(step.fallback); } } catch (_) { return false; }
      return true;
    }
    function validFeatures() {
      const features = overview?.();
      return Array.isArray(features) && features.length && features.every(feature => allowedRoute(feature?.route) && typeof feature.title === 'string' && typeof feature.body === 'string' && (!feature.action || typeof actions[feature.action] === 'function')) ? features : null;
    }
    function showOverview() {
      if (!checkContext()) return;
      const features = validFeatures(); if (!features) return;
      clearTargetWatch(); mode = 'overview'; expectedRoute = window.location.hash;
      card.classList.add('is-overview');
      for (const property of ['left','top','width','maxHeight']) card.style[property] = '';
      progress.textContent = 'Overview'; title.textContent = 'Explore Crucible'; body.replaceChildren();
      const list = node('div','orientation-features');
      for (const feature of features) {
        const choice = node('button','orientation-feature'+(feature.featured ? ' is-featured' : '')+(feature.action === 'points' ? ' orientation-points' : '')); choice.type = 'button';
        const icon = featureIcon(feature.icon);
        const copy = node('span','orientation-feature-copy'); copy.append(node('strong','',feature.title),node('span','',feature.body));
        const arrow = node('span','orientation-feature-arrow','→'); arrow.setAttribute('aria-hidden','true'); choice.append(icon,copy,arrow);
        choice.addEventListener('click',() => {
          if (!checkContext()) return;
          if (feature.action) { stop(false,'action'); actions[feature.action](); return; }
          const proposed = steps(feature.route); if (!validSteps(proposed)) return;
          itinerary = proposed.map(({route,target,title,body,fallback}) => ({route,target,title,body,fallback})); showStep(0);
        });
        list.append(choice);
      }
      body.append(list); back.hidden = next.hidden = directory.hidden = true; back.parentElement.hidden = true; begin.hidden = !getStarted; points.hidden = true;
      overlay.setAttribute('hidden',''); title.focus({preventScroll:true});
    }
    function watchTarget() {
      clearTargetWatch();
      const resolve = () => {
        if (!checkContext() || minimized) return;
        const step = itinerary[index];
        const candidate = Array.from(document.querySelectorAll(step.target)).find(visible) || (step.fallback && Array.from(document.querySelectorAll(step.fallback)).find(visible)) || null;
        if (target !== candidate) {
          target?.classList.remove('orientation-target'); target = candidate || null; target?.classList.add('orientation-target');
          const epoch = revealEpoch, owner = identity, route = expectedRoute;
          const reveal = () => {
            if (!active || minimized || revealEpoch !== epoch || context() !== owner || window.location.hash !== route || target !== candidate || !visible(candidate)) return;
            const anchor = presentation.anchor?.(candidate) || candidate;
            let box = anchor.getBoundingClientRect?.();
            if (box) {
              if (box.top < 12 || box.bottom > window.innerHeight-12) {
                anchor.scrollIntoView?.({block:'center',behavior:'instant'});
                box = anchor.getBoundingClientRect();
              }
              const prompt = card.getBoundingClientRect(), space = prompt.height+44;
              const crowded = window.innerWidth-box.width-32 < prompt.width+44 && box.top-16 < space && window.innerHeight-16-box.bottom < space;
              if (crowded && box.height+space+32 <= window.innerHeight) {
                // A tall phone card needs room for both its full outline and
                // the prompt; centering it can leave neither end clear.
                anchor.scrollIntoView?.({block:'start',behavior:'instant'});
                window.scrollBy?.({top:-16,behavior:'instant'});
              }
            }
            place();
          };
          reveal();
          // Navigation restores page top on its next frame. Reveal the native
          // control after that reset, while this exact step is still active.
          if (candidate && window.requestAnimationFrame) window.requestAnimationFrame(() => window.requestAnimationFrame(reveal));
          overlay.classList.remove('guide-arrive');
          if (target && overlay.getBoundingClientRect) { void overlay.getBoundingClientRect(); overlay.classList.add('guide-arrive'); }
        }
        place();
      };
      if (typeof MutationObserver === 'function') {
        observer = new MutationObserver(resolve);
        observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','class']});
      }
      deadline = window.setTimeout(() => { observer?.disconnect(); observer = null; deadline = null; },8000);
      resolve();
    }
    function setMinimized(value) {
      if (!active || context() !== identity) return false;
      if (!value && Array.from(document.querySelectorAll('dialog')).some(element => element !== dialog && element.open)) return false;
      if (!value && mode === 'overview') expectedRoute = window.location.hash;
      minimized = value;
      hide.textContent = minimized ? 'Continue guide' : 'Hide guide';
      hide.setAttribute('aria-label',hide.textContent); card.classList.toggle('is-minimized',minimized);
      if (minimized) {
        clearTargetWatch(); overlay.setAttribute('hidden',''); if (dialog.open) dialog.close();
        card.remove(); document.body.append(card); place(); hide.focus({preventScroll:true});
      } else {
        overlay.remove(); card.remove();
        dialog.append(overlay,card);
        try { dialog.showModal(); } catch (_) { stop(false,'unavailable'); return false; }
        if (mode === 'overview') showOverview();
        else if (overview && window.location.hash !== expectedRoute) {
          const proposed = steps();
          if (!validSteps(proposed)) { stop(true,'navigation'); return false; }
          itinerary = proposed.map(({route,target,title,body,fallback}) => ({route,target,title,body,fallback})); showStep(0);
        } else showStep(index);
      }
      return true;
    }
    function showStep(position) {
      if (context() !== identity) { stop(false,'context'); return; }
      clearTargetWatch(); mode = 'tour'; card.classList.remove('is-overview'); index = position; const step = itinerary[index]; expectedRoute = step.route;
      progress.textContent = (index+1)+' / '+itinerary.length; title.textContent = step.title; body.textContent = step.body;
      back.hidden = next.hidden = false; back.parentElement.hidden = false; directory.hidden = !overview; begin.hidden = points.hidden = true;
      back.disabled = index === 0; next.textContent = index === itinerary.length-1 ? 'Finish' : 'Next';
      try { if (!overview || window.location.hash !== step.route) navigate(step.route); } catch (_) { stop(true,'unavailable'); return; }
      if (!checkContext()) return;
      watchTarget(); title.focus({preventScroll:true});
    }
    function start() {
      if (active) return minimized ? setMinimized(false) : false;
      if (Array.from(document.querySelectorAll('dialog')).some(element => element.open)) return false;
      const currentIdentity = context(), showDirectory = Boolean(overview && isHome()), proposed = showDirectory ? [] : steps();
      if (typeof currentIdentity !== 'string' || !currentIdentity || !presentation || (showDirectory ? !validFeatures() : !validSteps(proposed))) return false;
      itinerary = proposed.map(({route,target,title,body,fallback}) => ({route,target,title,body,fallback})); identity = currentIdentity;
      const id = ++sequence;
      dialog = node('dialog','orientation-tour'); card = node('section','live-guide orientation-card'); overlay = presentation.create();
      progress = node('p','live-guide-progress orientation-progress');
      title = node('h2','orientation-title'); title.id = 'orientation-title-'+id; title.setAttribute('tabindex','-1');
      body = node('div','live-guide-copy orientation-body'); body.id = 'orientation-body-'+id;
      dialog.setAttribute('aria-labelledby',title.id); dialog.setAttribute('aria-describedby',body.id);
      const top = node('div','live-guide-top'), controls = node('div','guide-navigation orientation-controls');
      hide = node('button','guide-hide','Hide guide'); hide.setAttribute('aria-label','Hide guide');
      close = node('button','guide-close','×'); close.setAttribute('aria-label','Exit guide'); close.title = 'Exit guide';
      back = node('button','text-button guide-back','Back'); next = node('button','button button-primary guide-next','Next');
      directory = node('button','text-button orientation-directory','All features');
      begin = node('button','button button-primary orientation-begin','Start a guided challenge');
      points = node('button','text-button orientation-points','How points work');
      for (const button of [back,next,hide,close,directory,begin,points]) button.type = 'button';
      directory.addEventListener('click',showOverview);
      begin.addEventListener('click',() => { if (!checkContext() || !getStarted) return; stop(false,'action'); getStarted(); });
      points.addEventListener('click',() => { if (!checkContext() || !actions.points) return; stop(false,'action'); actions.points(); });
      back.addEventListener('click',() => { if (checkContext() && index > 0) showStep(index-1); });
      next.addEventListener('click',event => { event.stopPropagation(); if (!checkContext()) return; if (index+1 < itinerary.length) showStep(index+1); else stop(true,'complete'); });
      hide.addEventListener('click',() => setMinimized(!minimized)); close.addEventListener('click',event => { event.stopPropagation(); cancel(); });
      dialog.addEventListener('cancel',event => { event.preventDefault(); setMinimized(true); });
      dialog.addEventListener('close',() => { if (active && !minimized && !dialog.open) stop(); });
      dialog.addEventListener('click',event => { if (event.target === dialog) setMinimized(true); });
      dialog.addEventListener('keydown',event => {
        if (event.key !== 'Tab' || !checkContext()) return;
        const choices = [...card.querySelectorAll('.orientation-features button')];
        const focused = document.activeElement, last = mode === 'overview' ? begin.hidden ? choices[choices.length-1] || close : begin : next;
        if (!event.shiftKey && focused === last) { event.preventDefault(); hide.focus({preventScroll:true}); }
        else if (event.shiftKey && focused === hide) { event.preventDefault(); last.focus({preventScroll:true}); }
        else if (event.shiftKey && focused === title) { event.preventDefault(); close.focus({preventScroll:true}); }
      });
      const overviewActions = node('div','orientation-overview-actions'); overviewActions.append(begin);
      top.append(progress,hide,close); controls.append(back,directory,next); card.append(top,title,body,controls,overviewActions); dialog.append(overlay,card); document.body.append(dialog);
      try { dialog.showModal(); } catch (_) { dialog.remove(); itinerary = []; identity = null; return false; }
      active = true; minimized = false; document.addEventListener('click',outsideClick,true); onStart();
      window.addEventListener('hashchange',checkContext); window.addEventListener('popstate',checkContext);
      window.addEventListener('resize',place); window.addEventListener('scroll',place,true); window.addEventListener('pagehide',pagehide);
      expectedRoute = window.location.hash;
      if (showDirectory) showOverview(); else showStep(0); return active;
    }
    return Object.freeze({start,cancel,checkContext,minimize:() => setMinimized(true)});
  }
  window.CrucibleOrientation = Object.freeze({create});
})();
