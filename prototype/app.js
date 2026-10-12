'use strict';

const storageKey = 'crucible.recovery-draft.v2';
const form = document.querySelector('#contribution-form');
const submissionDialog = document.querySelector('#submission-dialog');
const aboutDialog = document.querySelector('#about-dialog');
const toast = document.querySelector('#toast');
let toastTimer;
let activeDraft = null;
let lastTrigger = null;

function showToast(message) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, 4500);
}
function validDraft(value) {
  return value && typeof value.methodName === 'string' && value.methodName.length <= 100 &&
    typeof value.recoveredKey === 'string' && /^(?:[0-9a-fA-F]{2})+$/.test(value.recoveredKey) &&
    value.recoveredKey.length <= 4096 && typeof value.pseudocode === 'string' &&
    value.pseudocode.length <= 15000 && typeof value.compute === 'string' && value.compute.length <= 4000;
}
try {
  const stored = JSON.parse(localStorage.getItem(storageKey));
  if (validDraft(stored)) activeDraft = stored;
  if (!activeDraft) {
    const previous = JSON.parse(localStorage.getItem('crucible.preliminary-draft.v1'));
    if (previous && typeof previous.methodName === 'string' && typeof previous.pseudocode === 'string' && typeof previous.compute === 'string') {
      form.elements.methodName.value = previous.methodName.slice(0, 100);
      form.elements.pseudocode.value = previous.pseudocode.slice(0, 15000);
      form.elements.compute.value = previous.compute.slice(0, 4000);
    }
  }
} catch { /* A blocked store must not stop the interface. */ }

function renderDraft() {
  document.querySelector('#saved-draft').hidden = !activeDraft;
  document.querySelector('#clear-draft').hidden = !activeDraft;
  if (!activeDraft) return;
  document.querySelector('#saved-draft-title').textContent = activeDraft.methodName;
  document.querySelector('#saved-draft-description').textContent =
    `Key attached (${activeDraft.recoveredKey.length / 2} bytes) · Pseudocode and compute estimate attached`;
}
function openDialog(dialog, trigger) {
  lastTrigger = trigger;
  dialog.showModal();
  document.body.style.overflow = 'hidden';
}
for (const button of document.querySelectorAll('[data-open-submission]')) {
  button.addEventListener('click', () => {
    if (activeDraft) {
      form.elements.methodName.value = activeDraft.methodName;
      form.elements.recoveredKey.value = activeDraft.recoveredKey;
      form.elements.pseudocode.value = activeDraft.pseudocode;
      form.elements.compute.value = activeDraft.compute;
    }
    openDialog(submissionDialog, button);
  });
}
for (const button of document.querySelectorAll('[data-open-about]')) {
  button.addEventListener('click', () => openDialog(aboutDialog, button));
}
for (const dialog of document.querySelectorAll('dialog')) {
  dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    document.body.style.overflow = '';
    lastTrigger?.focus();
  });
}

const tabs = [...document.querySelectorAll('[data-tab]')];
function activateTab(tab, focus = false) {
  tabs.forEach(item => {
    const selected = item === tab;
    item.setAttribute('aria-selected', String(selected));
    item.tabIndex = selected ? 0 : -1;
    document.querySelector(`#panel-${item.dataset.tab}`).hidden = !selected;
  });
  if (focus) tab.focus();
}
tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateTab(tab));
  tab.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    if (next !== undefined) { event.preventDefault(); activateTab(tabs[next], true); }
  });
});
document.querySelector('#copy-code').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(document.querySelector('#example-code').textContent);
    showToast('Example pseudocode copied.');
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(document.querySelector('#example-code'));
    selection.removeAllRanges();
    selection.addRange(range);
    showToast('Pseudocode selected. Use your usual copy shortcut.');
  }
});
document.querySelector('#use-example').addEventListener('click', () => {
  form.elements.pseudocode.value = document.querySelector('#example-code').textContent;
  form.elements.pseudocode.focus();
});
form.addEventListener('submit', event => {
  event.preventDefault();
  const values = new FormData(form);
  const nextDraft = {
    methodName: values.get('methodName').trim(), recoveredKey: values.get('recoveredKey').toLowerCase(),
    pseudocode: values.get('pseudocode').trim(), compute: values.get('compute').trim()
  };
  for (const field of ['methodName', 'recoveredKey', 'pseudocode', 'compute']) {
    if (!nextDraft[field]) {
      form.elements[field].value = '';
      form.elements[field].reportValidity();
      return;
    }
  }
  activeDraft = nextDraft;
  let persisted = true;
  try { localStorage.setItem(storageKey, JSON.stringify(activeDraft)); } catch { persisted = false; }
  renderDraft();
  submissionDialog.close();
  document.querySelector('#saved-draft').scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  showToast(persisted ? 'Draft saved in this browser. Nothing has been published.' : 'Draft kept for this visit. Browser storage is unavailable.');
});
document.querySelector('#clear-draft').addEventListener('click', () => {
  try { localStorage.removeItem(storageKey); } catch { /* Also allow deleting a memory-only draft. */ }
  activeDraft = null;
  form.reset();
  renderDraft();
  submissionDialog.close();
  showToast('Local contribution draft deleted.');
});
renderDraft();
