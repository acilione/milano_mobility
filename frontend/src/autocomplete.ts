interface Place { name: string; point: [number, number] }

const cache = new Map<string, Place[]>();

/** One address combobox, with request cancellation and keyboard selection. */
export function createAddressAutocomplete(
  editor: HTMLElement, onSelect: (place: Place) => void,
): { destroy(): void } {
  const input = editor.querySelector<HTMLInputElement>('input[type="search"]')!;
  const searchButton = editor.querySelector<HTMLButtonElement>('[data-search]')!;
  const list = editor.querySelector<HTMLElement>('.address-results')!;
  const message = editor.querySelector<HTMLElement>('.address-feedback')!;
  const listeners = new AbortController();
  let request: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let generation = 0, active = -1;
  let suggestions: Place[] = [];
  let composing = false;

  const close = (): void => {
    clearTimeout(timer); request?.abort(); generation++;
    suggestions = []; active = -1; list.replaceChildren(); list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    message.textContent = '';
  };
  const select = (index: number): void => {
    const result = suggestions[index];
    if (!result) return;
    close(); onSelect(result);
  };
  const highlight = (index: number): void => {
    active = index;
    [...list.children].forEach((option, i) => option.setAttribute('aria-selected', String(i === active)));
    const option = list.children[active];
    if (option) {
      input.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({block: 'nearest'});
    }
  };
  const search = async (): Promise<void> => {
    close();
    const query = input.value.trim();
    if (query.length < 3) { message.textContent = 'Enter at least 3 characters.'; return; }
    const token = generation;
    request = new AbortController();
    message.textContent = 'Searching Milan addresses…';
    try {
      let matches = cache.get(query.toLocaleLowerCase('it'));
      if (!matches) {
        const response = await fetch(`/api/places?q=${encodeURIComponent(query)}`, {signal: request.signal});
        const data = await response.json() as { places: Place[]; error?: string };
        if (!response.ok) throw new Error(data.error ?? 'Address suggestions are unavailable. Select a location on the map.');
        matches = data.places;
        if (token !== generation) return;
        if (cache.size >= 100) cache.delete(cache.keys().next().value!);
        cache.set(query.toLocaleLowerCase('it'), matches);
      }
      if (token !== generation) return;
      suggestions = matches;
      message.textContent = matches.length ? `${matches.length} suggestions. Use arrow keys and Enter to select.`
        : 'No matching Milan addresses. Refine the street name or select a location on the map.';
      list.hidden = !matches.length;
      input.setAttribute('aria-expanded', String(!!matches.length));
      matches.forEach((result, i) => {
        const option = document.createElement('button');
        option.type = 'button'; option.tabIndex = -1;
        option.id = `${input.id}-option-${i}`;
        option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false');
        option.textContent = result.name;
        // Keep keyboard focus in the combobox when selecting with a mouse or touch.
        option.addEventListener('pointerdown', event => event.preventDefault());
        option.addEventListener('click', () => select(i));
        list.append(option);
      });
    } catch (error) {
      if (token === generation) message.textContent = error instanceof Error ? error.message : 'Address suggestions are unavailable.';
    }
  };
  const schedule = (): void => {
    close();
    if (!composing && input.value.trim().length >= 3) timer = setTimeout(() => void search(), 600);
  };
  input.addEventListener('input', schedule, {signal: listeners.signal});
  input.addEventListener('compositionstart', () => { composing = true; close(); }, {signal: listeners.signal});
  input.addEventListener('compositionend', () => { composing = false; schedule(); }, {signal: listeners.signal});
  input.addEventListener('keydown', event => {
    if (event.isComposing || composing) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (suggestions.length) highlight(active < 0 ? (event.key === 'ArrowDown' ? 0 : suggestions.length - 1)
        : (active + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length);
      else void search();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (active >= 0) select(active); else void search();
    } else if (event.key === 'Tab') close();
  }, {signal: listeners.signal});
  editor.addEventListener('focusout', event => {
    if (!editor.contains(event.relatedTarget as Node | null)) close();
  }, {signal: listeners.signal});
  searchButton.addEventListener('click', () => { input.focus(); void search(); }, {signal: listeners.signal});
  return { destroy(): void { close(); listeners.abort(); } };
}
