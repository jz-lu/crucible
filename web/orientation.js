/* Optional orientation through existing read-only screens. No application actions. */
(() => {
  'use strict';
  const routes = new Set(['#challenge', '#notebook', '#bounties', '#activity?kind=notes']);
  let sequence = 0;
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  function create({launcher, context, navigate, steps}) {
    let active = false, identity = null, itinerary = [], index = 0, expectedRoute = null;
    let dialog, card, ring, progress, title, body, back, next;
    let observer = null, deadline = null, target = null;

    function visible(element) {
      if (!element?.isConnected || !element.getClientRects().length) return false;
      const style = window.getComputedStyle(element);
      return style.visibility !== 'hidden' && style.visibility !== 'collapse';
    }

    function clearTargetWatch() {
      observer?.disconnect(); observer = null;
      if (deadline !== null) window.clearTimeout(deadline);
      deadline = null;
    }

    function restoreFocus() {
      const destination = visible(launcher) ? launcher : Array.from(document.querySelectorAll('h1, h2')).find(visible);
      if (!destination) return;
      const addedTabIndex = !destination.hasAttribute('tabindex') && destination !== launcher;
      if (addedTabIndex) destination.setAttribute('tabindex', '-1');
      destination.focus({preventScroll: true});
      if (addedTabIndex) destination.removeAttribute('tabindex');
    }

    function stop(restore = true) {
      if (!active) return;
      active = false;
      clearTargetWatch();
      window.removeEventListener('hashchange', checkContext);
      window.removeEventListener('popstate', checkContext);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('pagehide', pagehide);
      const closing = dialog;
      if (closing.open) closing.close();
      closing.remove();
      target = null; identity = null; itinerary = []; expectedRoute = null;
      if (restore) restoreFocus();
    }

    function cancel() { stop(); }
    function pagehide() { stop(false); }
    function checkContext() {
      if (!active) return false;
      if (context() !== identity || window.location.hash !== expectedRoute) { stop(); return false; }
      return true;
    }

    function place() {
      if (!checkContext()) return;
      const width = window.innerWidth, height = window.innerHeight, margin = 16;
      const rect = visible(target) ? target.getBoundingClientRect() : null;
      const inView = rect && rect.bottom > 0 && rect.right > 0 && rect.top < height && rect.left < width;
      if (ring.hidden !== !inView) ring.hidden = !inView;
      if (inView) {
        const left = Math.max(4, rect.left - 6), top = Math.max(4, rect.top - 6);
        ring.style.left = left + 'px'; ring.style.top = top + 'px';
        ring.style.width = Math.max(0, Math.min(width - 4, rect.right + 6) - left) + 'px';
        ring.style.height = Math.max(0, Math.min(height - 4, rect.bottom + 6) - top) + 'px';
      }
      const size = card.getBoundingClientRect();
      const cardWidth = size.width, cardHeight = size.height;
      const right = Math.max(margin, width - cardWidth - margin), bottom = Math.max(margin, height - cardHeight - margin);
      const candidates = width <= 640 ? [[Math.max(margin, (width - cardWidth) / 2), bottom]] : [[right, bottom], [margin, bottom], [right, margin], [margin, margin]];
      const overlap = ([left, top]) => !inView ? 0 : Math.max(0, Math.min(left + cardWidth, rect.right + 12) - Math.max(left, rect.left - 12)) * Math.max(0, Math.min(top + cardHeight, rect.bottom + 12) - Math.max(top, rect.top - 12));
      let best = candidates[0];
      for (const candidate of candidates) if (overlap(candidate) < overlap(best)) best = candidate;
      card.style.left = best[0] + 'px'; card.style.top = best[1] + 'px';
    }

    function watchTarget() {
      clearTargetWatch(); target = null;
      const resolve = () => {
        if (!checkContext()) return;
        const candidate = document.querySelector(itinerary[index].target);
        target = visible(candidate) ? candidate : null;
        place();
      };
      // Route rendering can finish after the heading and controls are shown.
      // The tour remains usable even when this target never becomes available.
      observer = new MutationObserver(resolve);
      observer.observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'class']});
      deadline = window.setTimeout(clearTargetWatch, 8000);
      resolve();
    }

    function showStep(position) {
      if (context() !== identity) { stop(); return; }
      clearTargetWatch(); index = position;
      const step = itinerary[index];
      expectedRoute = step.route;
      progress.textContent = 'Step ' + (index + 1) + ' of ' + itinerary.length;
      title.textContent = step.title; body.textContent = step.body;
      back.disabled = index === 0;
      next.textContent = index === itinerary.length - 1 ? 'Finish' : 'Next';
      ring.hidden = true; target = null;
      try { navigate(step.route); } catch (_) { stop(); return; }
      if (!checkContext()) return;
      watchTarget(); title.focus({preventScroll: true});
    }

    function start() {
      if (active || Array.from(document.querySelectorAll('dialog')).some(element => element.open)) return false;
      const currentIdentity = context();
      if (typeof currentIdentity !== 'string' || !currentIdentity) return false;
      const proposed = steps();
      if (!Array.isArray(proposed) || !proposed.length || proposed.some(step => !routes.has(step?.route) || typeof step.target !== 'string' || !step.target || typeof step.title !== 'string' || typeof step.body !== 'string')) return false;
      // Validate selectors before opening; callers supply selectors, never copied page data.
      try { for (const step of proposed) document.querySelector(step.target); } catch (_) { return false; }
      itinerary = proposed.map(({route, target, title, body}) => ({route, target, title, body}));
      identity = currentIdentity;
      const id = ++sequence;
      dialog = node('dialog', 'orientation-tour');
      ring = node('div', 'orientation-ring'); ring.hidden = true; ring.setAttribute('aria-hidden', 'true');
      card = node('section', 'orientation-card');
      progress = node('p', 'orientation-progress');
      title = node('h2', 'orientation-title'); title.id = 'orientation-title-' + id; title.setAttribute('tabindex', '-1');
      body = node('p', 'orientation-body'); body.id = 'orientation-body-' + id;
      dialog.setAttribute('aria-labelledby', title.id); dialog.setAttribute('aria-describedby', body.id);
      const controls = node('div', 'orientation-controls');
      back = node('button', 'button button-quiet', 'Back');
      next = node('button', 'button button-primary', 'Next');
      const close = node('button', 'text-button orientation-close', 'Close tour');
      for (const button of [back, next, close]) button.type = 'button';
      back.addEventListener('click', () => { if (checkContext() && index > 0) showStep(index - 1); });
      next.addEventListener('click', () => { if (!checkContext()) return; if (index + 1 < itinerary.length) showStep(index + 1); else stop(); });
      close.addEventListener('click', cancel);
      dialog.addEventListener('cancel', event => { event.preventDefault(); cancel(); });
      dialog.addEventListener('close', cancel);
      dialog.addEventListener('click', event => { if (event.target === dialog) cancel(); });
      dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab' || !checkContext()) return;
        const first = back.disabled ? next : back;
        const focused = document.activeElement;
        if (!event.shiftKey && focused === close) {
          event.preventDefault(); first.focus({preventScroll: true});
        } else if (event.shiftKey && (focused === first || focused === title)) {
          event.preventDefault(); close.focus({preventScroll: true});
        }
      });
      controls.append(back, next, close); card.append(progress, title, body, controls); dialog.append(ring, card);
      document.body.append(dialog);
      try { dialog.showModal(); } catch (_) { dialog.remove(); itinerary = []; identity = null; return false; }
      active = true;
      window.addEventListener('hashchange', checkContext);
      window.addEventListener('popstate', checkContext);
      window.addEventListener('resize', place);
      window.addEventListener('scroll', place, true);
      window.addEventListener('pagehide', pagehide);
      showStep(0);
      return active;
    }

    return Object.freeze({start, cancel, checkContext});
  }

  window.CrucibleOrientation = Object.freeze({create});
})();
