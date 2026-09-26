/**
 * HA Desktop Widget admin panel.
 *
 * Lists registered desktops and stored profiles, assigns profiles, and edits profiles in a live
 * preview: the desktop app's real renderer running in a same-origin iframe (frontend/preview,
 * built from HA Desktop Widget by scripts/update_preview.sh). The preview exposes
 * `window.__hadwPreview`; Home Assistant data reaches it only through this panel.
 */

const DOMAIN = 'ha_desktop_widget';
const DEFAULT_SIZE = { width: 420, height: 640 };
const PREVIEW_READY_TIMEOUT_MS = 15000;

function escapeHtml(value) {
  return String(value ?? '').replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]
  );
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value ?? null);
}

function sameJson(left, right) {
  return stableJson(left) === stableJson(right);
}

function titleCase(value) {
  const text = String(value || '');
  return text ? text[0].toUpperCase() + text.slice(1) : '';
}

/**
 * The preview always reports every profile section, filled with its defaults. A profile only
 * overwrites the sections it contains on a desktop, so start from the stored profile and take
 * only the sections the editor actually changed.
 */
function mergeEditedSections(original, baseline, draft) {
  const merged = { ...(original || {}) };
  for (const [key, value] of Object.entries(draft || {})) {
    if (!sameJson(value, baseline?.[key])) merged[key] = value;
  }
  return merged;
}

// Empty values for every shareable section (HA Desktop Widget profile schema v1). The preview's
// own defaults omit some sections, and an omitted section would keep the previous selection's.
const EMPTY_SECTIONS = Object.freeze({
  primaryCards: [],
  favoriteEntities: [],
  customTabs: [],
  activeTabId: '',
  comparisonGraphs: [],
  quickAccessTileOptions: {},
  customEntityIcons: {},
  customEntityNames: {},
});

/**
 * The preview merges each applied document over whatever it showed before. Layer the document
 * over explicit values for every section so nothing from a previous selection leaks through.
 */
function withPreviewDefaults(pristine, document) {
  const layered = { ...EMPTY_SECTIONS, ...(pristine || {}), ...(document || {}) };
  if (pristine?.ui) layered.ui = { ...pristine.ui, ...(document?.ui || {}) };
  return layered;
}

const STYLE = `
  :host {
    display: block;
    height: 100%;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
    --hadw-radius: var(--ha-card-border-radius, 12px);
  }
  * { box-sizing: border-box; }
  header {
    display: flex;
    align-items: center;
    gap: 4px;
    height: var(--header-height, 56px);
    padding: 0 12px;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, var(--text-primary-color));
    border-bottom: var(--app-header-border-bottom, none);
  }
  header h1 { margin: 0 0 0 8px; font-size: 20px; font-weight: 400; }
  .layout {
    display: grid;
    grid-template-columns: minmax(280px, 340px) 1fr;
    height: calc(100% - var(--header-height, 56px));
  }
  :host([narrow]) .layout { grid-template-columns: 1fr; height: auto; }
  aside {
    overflow-y: auto;
    padding: 16px;
    border-right: 1px solid var(--divider-color);
  }
  :host([narrow]) aside { border-right: none; border-bottom: 1px solid var(--divider-color); }
  main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  section + section { margin-top: 24px; }
  .section-head { display: flex; align-items: center; justify-content: space-between; }
  h2 {
    margin: 0 0 8px;
    font-size: 14px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color);
  }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  .card {
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: var(--hadw-radius);
  }
  .item {
    display: block;
    width: 100%;
    padding: 10px 12px;
    text-align: left;
    font: inherit;
    color: inherit;
    background: none;
    border: none;
    border-radius: var(--hadw-radius);
    cursor: pointer;
  }
  .item:hover { background: var(--secondary-background-color); }
  .selected { outline: 2px solid var(--primary-color); outline-offset: -2px; }
  .name { font-weight: 500; overflow-wrap: anywhere; }
  .meta { display: block; margin-top: 2px; font-size: 13px; color: var(--secondary-text-color); }
  .desktop-controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    padding: 0 12px 10px;
  }
  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    margin-right: 6px;
    border-radius: 50%;
    vertical-align: middle;
    background: var(--disabled-text-color, #9e9e9e);
  }
  .dot.online { background: var(--success-color, #43a047); }
  .chip {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 12px;
    background: var(--secondary-background-color);
    color: var(--secondary-text-color);
  }
  .chip.warn { background: var(--warning-color, #ffa600); color: #000; }
  .chip.ok { background: var(--success-color, #43a047); color: #fff; }
  .chip.accent { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.action, select, input {
    font: inherit;
    font-size: 14px;
    color: var(--primary-text-color);
    background: var(--card-background-color);
    border: 1px solid var(--divider-color);
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 34px;
  }
  button.action { cursor: pointer; }
  button.action:hover:not(:disabled) { background: var(--secondary-background-color); }
  button.primary {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  button.primary:hover:not(:disabled) { filter: brightness(1.08); background: var(--primary-color); }
  button.danger { color: var(--error-color, #db4437); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.pressed { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  label.inline { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
  .form { display: grid; gap: 8px; padding: 12px; margin-bottom: 8px; }
  .form .row { display: flex; gap: 8px; justify-content: flex-end; }
  .empty { margin: 0; font-size: 14px; color: var(--secondary-text-color); }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--divider-color);
  }
  .toolbar .grow { flex: 1 1 auto; }
  .toolbar input.title { font-size: 16px; font-weight: 500; min-width: 180px; }
  .banner { margin: 12px 16px 0; padding: 10px 12px; border-radius: 8px; font-size: 14px; }
  .banner.error { background: var(--error-color, #db4437); color: #fff; }
  .banner.info { background: var(--secondary-background-color); }
  .stage {
    flex: 1 1 auto;
    overflow: auto;
    padding: 24px;
    display: flex;
    align-items: flex-start;
    min-height: 480px;
  }
  :host([narrow]) .stage { padding: 16px; }
  .frame {
    flex: none;
    /* Auto margins centre the preview but, unlike justify-content, keep an oversized one
       scrollable instead of clipping its left edge. */
    margin: 0 auto;
    border: 1px solid var(--divider-color);
    border-radius: var(--hadw-radius);
    overflow: hidden;
    box-shadow: var(--ha-card-box-shadow, 0 2px 12px rgba(0, 0, 0, 0.18));
    background: #000;
  }
  .frame iframe { display: block; width: 100%; height: 100%; border: 0; }
  .placeholder {
    max-width: 460px;
    margin: 48px auto 0;
    text-align: center;
    color: var(--secondary-text-color);
  }
  .hidden { display: none !important; }
`;

class HaDesktopWidgetPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._panel = null;
    this._desktops = [];
    this._profiles = [];
    this._loaded = false;
    this._selection = null; // { type: 'profile' | 'desktop', id }
    this._original = null; // stored document of the selection
    this._baseline = null; // preview document right after loading the selection
    this._draft = null; // preview document now
    this._loadedRevision = null;
    this._loadedSnapshotAt = null; // updated_at of the desktop snapshot being shown
    this._name = '';
    this._loadedName = ''; // name as loaded, to tell a rename apart from no change
    this._editing = false;
    this._entityInput = '';
    this._sizeSource = '';
    this._creating = null; // { name, source }
    this._busy = false;
    this._error = '';
    this._notice = '';
    this._unsubscribe = null;
    this._subscribing = false;
    this._previewApi = null;
    this._previewDefaults = null;
    this._previewPromise = null;
    this._pushedStates = null;
    this._stateFrame = 0;
    this._checkTimer = 0;
    this._built = false;
  }

  set hass(hass) {
    const previous = this._hass;
    this._hass = hass;
    if (!this._built) this._build();
    if (this.isConnected) this._subscribe();
    if (!previous || previous.states !== hass.states) this._scheduleStatePush();
    const menu = this.shadowRoot.querySelector('ha-menu-button');
    if (menu) menu.hass = hass;
  }

  get hass() {
    return this._hass;
  }

  set narrow(narrow) {
    this.toggleAttribute('narrow', !!narrow);
    const menu = this.shadowRoot.querySelector('ha-menu-button');
    if (menu) menu.narrow = !!narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  set route(_route) {}

  connectedCallback() {
    if (!this._built) this._build();
    if (this._hass) this._subscribe();
  }

  disconnectedCallback() {
    if (this._unsubscribe) {
      this._unsubscribe();
      this._unsubscribe = null;
    }
  }

  // --- data -------------------------------------------------------------------------------

  async _subscribe() {
    if (this._unsubscribe || this._subscribing || !this._hass?.connection) return;
    this._subscribing = true;
    try {
      this._unsubscribe = await this._hass.connection.subscribeMessage(
        (update) => this._onUpdate(update),
        { type: `${DOMAIN}/subscribe_updates` }
      );
    } catch (error) {
      this._showError(error);
    } finally {
      this._subscribing = false;
    }
  }

  _onUpdate(update) {
    this._desktops = [...(update.desktops || [])].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
    );
    this._profiles = [...(update.profiles || [])].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
    );
    this._loaded = true;

    const selection = this._selection;
    if (selection?.type === 'profile') {
      const profile = this._profiles.find((item) => item.profile_id === selection.id);
      if (!profile) {
        this._clearSelection();
        this._notice = 'The profile you were viewing was deleted.';
      } else if (profile.revision !== this._loadedRevision && !this._isDirty()) {
        void this._loadSelection();
      } else if (profile.name !== this._loadedName && this._name.trim() === this._loadedName) {
        // Renamed elsewhere and not renamed here: follow it, or a later save would revert it.
        this._name = profile.name;
        this._loadedName = profile.name;
      }
    } else if (selection?.type === 'desktop') {
      const desktop = this._desktops.find((item) => item.desktop_id === selection.id);
      if (!desktop) {
        this._clearSelection();
      } else if (
        desktop.snapshot_updated_at !== this._loadedSnapshotAt &&
        this._draft &&
        !this._isDirty()
      ) {
        // The desktop reported a newer layout; show it rather than a stale copy.
        void this._loadSelection();
      }
    }
    this._renderSidebar();
    this._renderToolbar();
    this._renderStage();
  }

  async _call(message) {
    return this._hass.callWS(message);
  }

  async _run(task) {
    if (this._busy) return undefined;
    this._busy = true;
    this._error = '';
    this._notice = '';
    this._renderAll();
    try {
      return await task();
    } catch (error) {
      this._showError(error);
      return undefined;
    } finally {
      this._busy = false;
      this._renderAll();
    }
  }

  _showError(error) {
    this._error =
      error?.code === 'revision_conflict'
        ? `${error.message}. Your changes are still here: note them, choose Discard to load the ` +
          'latest revision, then make them again.'
        : error?.message || String(error);
    this._renderBanners();
  }

  // --- selection --------------------------------------------------------------------------

  _isDirty() {
    if (!this._draft) return false;
    return !sameJson(this._draft, this._baseline) || this._name.trim() !== this._loadedName;
  }

  _confirmDiscard() {
    return !this._isDirty() || window.confirm('Discard unsaved changes to this layout?');
  }

  _clearSelection() {
    this._selection = null;
    this._original = null;
    this._baseline = null;
    this._draft = null;
    this._loadedRevision = null;
    this._editing = false;
    this._renderAll();
  }

  _select(selection) {
    // A load or save in flight belongs to the current selection; switching mid-way would pair
    // one profile's draft with another's identity.
    if (this._busy || !this._confirmDiscard()) return;
    this._selection = selection;
    // Forget the previous selection's documents before loading, so a failed load cannot leave
    // them editable (and savable) under the new identity.
    this._original = null;
    this._baseline = null;
    this._draft = null;
    this._loadedRevision = null;
    this._editing = false;
    this._error = '';
    this._notice = '';
    if (selection.type === 'desktop') {
      const desktop = this._desktops.find((item) => item.desktop_id === selection.id);
      this._name = desktop ? `${desktop.name} layout` : '';
      this._sizeSource = selection.id;
    } else {
      const profile = this._profiles.find((item) => item.profile_id === selection.id);
      this._name = profile?.name || '';
    }
    void this._loadSelection();
  }

  async _loadSelection() {
    const selection = this._selection;
    if (!selection) return;
    await this._run(async () => {
      let document;
      let revision = null;
      if (selection.type === 'profile') {
        const profile = await this._call({
          type: `${DOMAIN}/profiles/get`,
          profile_id: selection.id,
        });
        document = profile.document;
        revision = profile.revision;
        this._name = profile.name;
      } else {
        const snapshot = await this._call({
          type: `${DOMAIN}/desktops/get_snapshot`,
          desktop_id: selection.id,
        });
        document = snapshot.document;
        this._loadedSnapshotAt = snapshot.updated_at;
      }
      if (this._selection !== selection) return;
      const api = await this._ensurePreview();
      api.setEditing(false);
      await api.applyProfile(structuredClone(withPreviewDefaults(this._previewDefaults, document)));
      this._original = document;
      this._loadedRevision = revision;
      this._loadedName = this._name.trim();
      this._baseline = structuredClone(api.getDocument());
      this._draft = structuredClone(this._baseline);
    });
  }

  // --- preview ----------------------------------------------------------------------------

  _ensurePreview() {
    if (this._previewApi) return Promise.resolve(this._previewApi);
    if (this._previewPromise) return this._previewPromise;
    const iframe = this.shadowRoot.querySelector('iframe');
    this._previewPromise = new Promise((resolve, reject) => {
      const started = Date.now();
      const poll = () => {
        const api = iframe.contentWindow?.__hadwPreview;
        if (api?.ready) {
          resolve(this._attachPreview(iframe, api));
        } else if (Date.now() - started > PREVIEW_READY_TIMEOUT_MS) {
          this._previewPromise = null;
          reject(new Error('The widget preview did not load. Reload the page to try again.'));
        } else {
          setTimeout(poll, 100);
        }
      };
      if (!iframe.getAttribute('src')) iframe.setAttribute('src', this._previewUrl());
      poll();
    });
    return this._previewPromise;
  }

  _previewUrl() {
    return (
      this._panel?.config?.preview_url || `/${DOMAIN}_static/preview/preview.html`
    );
  }

  _attachPreview(iframe, api) {
    this._previewApi = api;
    this._previewDefaults = structuredClone(api.getDocument());
    this._pushedStates = null;
    this._pushStates();
    api.onDocumentChange = () => this._scheduleDocumentCheck();
    // Tile edits made inside the preview do not raise onDocumentChange, so re-read the document
    // after any interaction there.
    const doc = iframe.contentDocument;
    for (const type of ['pointerup', 'click', 'change', 'input', 'keyup', 'drop']) {
      doc.addEventListener(type, () => this._scheduleDocumentCheck(), true);
    }
    return api;
  }

  _scheduleDocumentCheck() {
    clearTimeout(this._checkTimer);
    this._checkTimer = setTimeout(() => this._checkDocument(), 150);
  }

  _checkDocument() {
    if (!this._previewApi || !this._selection || !this._draft) return;
    const current = this._previewApi.getDocument();
    if (sameJson(current, this._draft)) return;
    this._draft = structuredClone(current);
    this._renderToolbar();
  }

  _scheduleStatePush() {
    if (this._stateFrame || !this._previewApi) return;
    this._stateFrame = requestAnimationFrame(() => {
      this._stateFrame = 0;
      this._pushStates();
    });
  }

  _pushStates() {
    const api = this._previewApi;
    const states = this._hass?.states;
    if (!api || !states) return;
    const removed =
      this._pushedStates && Object.keys(this._pushedStates).some((entityId) => !(entityId in states));
    if (!this._pushedStates || removed) {
      // The preview has no per-entity removal, so a deleted entity means replacing the set.
      api.setStates({ ...states });
    } else {
      for (const [entityId, state] of Object.entries(states)) {
        if (this._pushedStates[entityId] !== state) api.setEntityState(state);
      }
    }
    this._pushedStates = states;
  }

  _applySize() {
    const frame = this.shadowRoot.querySelector('.frame');
    const desktop = this._desktops.find((item) => item.desktop_id === this._sizeSource);
    const width = desktop?.window_width || DEFAULT_SIZE.width;
    const height = desktop?.window_height || DEFAULT_SIZE.height;
    frame.style.width = `${width}px`;
    frame.style.height = `${height}px`;
  }

  // --- actions ----------------------------------------------------------------------------

  _handleClick(event) {
    const target = event.target.closest('[data-action]');
    if (!target || target.disabled) return;
    const { action, id } = target.dataset;
    switch (action) {
      case 'select-profile':
        this._select({ type: 'profile', id });
        break;
      case 'select-desktop':
        this._select({ type: 'desktop', id });
        break;
      case 'new-profile':
        this._creating = { name: '', source: '' };
        this._renderSidebar();
        this.shadowRoot.querySelector('[data-field="create-name"]')?.focus();
        break;
      case 'cancel-create':
        this._creating = null;
        this._renderSidebar();
        break;
      case 'create':
        void this._createProfile();
        break;
      case 'toggle-edit':
        this._toggleEditing();
        break;
      case 'add-entity':
        this._addEntity();
        break;
      case 'discard':
        if (this._confirmDiscard()) void this._loadSelection();
        break;
      case 'save':
        void this._save();
        break;
      case 'delete':
        void this._deleteProfile();
        break;
      case 'dismiss':
        this._error = '';
        this._notice = '';
        this._renderBanners();
        break;
      default:
        break;
    }
  }

  _handleChange(event) {
    const target = event.target;
    const field = target.dataset.field;
    if (field === 'assign') {
      void this._assign(target.dataset.id, target.value || null);
    } else if (field === 'size') {
      this._sizeSource = target.value;
      this._applySize();
    } else if (field === 'create-source' && this._creating) {
      this._creating.source = target.value;
    }
  }

  _handleInput(event) {
    const target = event.target;
    const field = target.dataset.field;
    if (field === 'name') {
      this._name = target.value;
      this._updateSaveState();
    } else if (field === 'entity') {
      this._entityInput = target.value;
    } else if (field === 'create-name' && this._creating) {
      this._creating.name = target.value;
    }
  }

  _handleKeydown(event) {
    if (event.key !== 'Enter') return;
    const field = event.target.dataset.field;
    if (field === 'entity') this._addEntity();
    if (field === 'create-name') void this._createProfile();
  }

  async _assign(desktopId, profileId) {
    await this._run(async () => {
      await this._call({
        type: `${DOMAIN}/desktops/assign_profile`,
        desktop_id: desktopId,
        profile_id: profileId,
      });
    });
  }

  async _createProfile() {
    const creating = this._creating;
    const name = creating?.name.trim();
    if (!name) {
      this._showError(new Error('Enter a name for the new profile.'));
      return;
    }
    const created = await this._run(async () => {
      let document = {};
      if (creating.source) {
        const snapshot = await this._call({
          type: `${DOMAIN}/desktops/get_snapshot`,
          desktop_id: creating.source,
        });
        document = snapshot.document;
      }
      return this._call({ type: `${DOMAIN}/profiles/save`, name, document });
    });
    if (!created) return;
    this._creating = null;
    this._upsertProfile(created);
    this._select({ type: 'profile', id: created.profile_id });
  }

  _toggleEditing() {
    if (!this._previewApi) return;
    this._editing = this._previewApi.setEditing(!this._editing);
    this._renderToolbar();
  }

  _addEntity() {
    const entityId = this._entityInput.trim();
    if (!entityId || !this._previewApi) return;
    if (!this._hass.states[entityId]) {
      this._showError(new Error(`${entityId} is not an entity in Home Assistant.`));
      return;
    }
    this._previewApi.addEntity(entityId);
    this._entityInput = '';
    const input = this.shadowRoot.querySelector('[data-field="entity"]');
    if (input) input.value = '';
    this._scheduleDocumentCheck();
  }

  async _save() {
    const selection = this._selection;
    const name = this._name.trim();
    if (!selection || !this._draft || !name) return;
    // The preview stays editable while the request is in flight, so remember exactly what was
    // sent: only that becomes the saved baseline, and later edits stay unsaved.
    const sentDraft = structuredClone(this._draft);
    const saved = await this._run(async () => {
      if (selection.type === 'profile') {
        return this._call({
          type: `${DOMAIN}/profiles/save`,
          profile_id: selection.id,
          // Refused if someone else saved since this editor loaded the profile.
          ...(Number.isInteger(this._loadedRevision)
            ? { expected_revision: this._loadedRevision }
            : {}),
          name,
          document: mergeEditedSections(this._original, this._baseline, sentDraft),
        });
      }
      // A desktop layout is complete, so it becomes a profile as shown.
      return this._call({ type: `${DOMAIN}/profiles/save`, name, document: sentDraft });
    });
    if (!saved) return;
    this._upsertProfile(saved);
    if (selection.type === 'profile') {
      this._original = saved.document;
      this._baseline = sentDraft;
      this._loadedRevision = saved.revision;
      if (this._name.trim() === name) this._name = saved.name;
      this._loadedName = saved.name;
      this._notice = `Saved revision ${saved.revision}. Desktops using this profile update when they are online.`;
      this._renderAll();
    } else {
      this._baseline = sentDraft;
      this._loadedName = name;
      this._select({ type: 'profile', id: saved.profile_id });
      this._notice = `Saved as profile ${saved.name}.`;
    }
  }

  async _deleteProfile() {
    const selection = this._selection;
    if (selection?.type !== 'profile') return;
    const profile = this._profiles.find((item) => item.profile_id === selection.id);
    const assigned = this._desktops.filter((item) => item.assigned_profile_id === selection.id);
    const detail = assigned.length
      ? ` It is assigned to ${assigned.length} desktop${assigned.length === 1 ? '' : 's'}; they keep their current layout.`
      : '';
    if (!window.confirm(`Delete profile ${profile?.name || ''}?${detail}`)) return;
    const deleted = await this._run(async () => {
      await this._call({ type: `${DOMAIN}/profiles/delete`, profile_id: selection.id });
      return true;
    });
    if (!deleted) return;
    this._profiles = this._profiles.filter((item) => item.profile_id !== selection.id);
    this._baseline = this._draft;
    this._clearSelection();
    this._notice = 'Profile deleted.';
    this._renderAll();
  }

  _upsertProfile(profile) {
    const summary = { ...profile, sections: Object.keys(profile.document || {}).sort() };
    delete summary.document;
    this._profiles = [
      ...this._profiles.filter((item) => item.profile_id !== profile.profile_id),
      summary,
    ].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }

  // --- rendering --------------------------------------------------------------------------

  _build() {
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <header>
        <ha-menu-button></ha-menu-button>
        <h1>Desktop Widgets</h1>
      </header>
      <div class="layout">
        <aside></aside>
        <main>
          <div class="toolbar hidden"></div>
          <div class="banners"></div>
          <div class="stage">
            <div class="placeholder"></div>
            <div class="frame hidden"><iframe title="Widget preview"></iframe></div>
          </div>
        </main>
      </div>
    `;
    const root = this.shadowRoot;
    root.addEventListener('click', (event) => this._handleClick(event));
    root.addEventListener('change', (event) => this._handleChange(event));
    root.addEventListener('input', (event) => this._handleInput(event));
    root.addEventListener('keydown', (event) => this._handleKeydown(event));
    // Start loading the preview straight away so the first selection is quick.
    void this._ensurePreview().catch((error) => this._showError(error));
    this._renderAll();
  }

  _renderAll() {
    this._renderSidebar();
    this._renderToolbar();
    this._renderBanners();
    this._renderStage();
  }

  // Live updates re-render the sidebar and toolbar; put focus and the caret back where they
  // were, or typing in a form would be interrupted by every desktop heartbeat.
  _keepingFocus(render) {
    const active = this.shadowRoot.activeElement;
    const field = active?.dataset?.field;
    const id = active?.dataset?.id;
    let start = null;
    let end = null;
    try {
      start = active?.selectionStart ?? null;
      end = active?.selectionEnd ?? null;
    } catch {
      /* not a text control */
    }
    render();
    if (!field) return;
    const selector = `[data-field="${field}"]${id ? `[data-id="${CSS.escape(id)}"]` : ''}`;
    const next = this.shadowRoot.querySelector(selector);
    if (!next || next === active) return;
    next.focus();
    if (start !== null && typeof next.setSelectionRange === 'function') {
      try {
        next.setSelectionRange(start, end);
      } catch {
        /* not a text control */
      }
    }
  }

  _renderSidebar() {
    this._keepingFocus(() => this._renderSidebarContent());
  }

  _renderSidebarContent() {
    const aside = this.shadowRoot.querySelector('aside');
    if (!aside) return;
    if (!this._loaded) {
      aside.innerHTML = `<p class="empty">Loading…</p>`;
      return;
    }
    const selection = this._selection;
    const snapshotDesktops = this._desktops.filter((item) => item.has_snapshot);
    const createForm = this._creating
      ? `<div class="card form">
          <label class="inline" style="display:grid">Name
            <input data-field="create-name" maxlength="64" value="${escapeHtml(this._creating.name)}">
          </label>
          <label class="inline" style="display:grid">Start from
            <select data-field="create-source">
              <option value="">An empty profile</option>
              ${snapshotDesktops
                .map(
                  (item) =>
                    `<option value="${escapeHtml(item.desktop_id)}" ${
                      this._creating.source === item.desktop_id ? 'selected' : ''
                    }>The layout of ${escapeHtml(item.name)}</option>`
                )
                .join('')}
            </select>
          </label>
          <div class="row">
            <button class="action" data-action="cancel-create">Cancel</button>
            <button class="action primary" data-action="create" ${this._busy ? 'disabled' : ''}>Create</button>
          </div>
        </div>`
      : '';

    const profiles = this._profiles.length
      ? `<ul>${this._profiles
          .map((profile) => {
            const count = this._desktops.filter(
              (item) => item.assigned_profile_id === profile.profile_id
            ).length;
            const selected =
              selection?.type === 'profile' && selection.id === profile.profile_id;
            return `<li class="card ${selected ? 'selected' : ''}">
              <button class="item" data-action="select-profile" data-id="${escapeHtml(profile.profile_id)}" ${
                this._busy ? 'disabled' : ''
              }>
                <span class="name">${escapeHtml(profile.name)}</span>
                <span class="meta">Revision ${profile.revision} · ${count} desktop${count === 1 ? '' : 's'}</span>
              </button>
            </li>`;
          })
          .join('')}</ul>`
      : `<p class="empty">No profiles yet. Arrange the widget on a desktop, then create a profile from its layout.</p>`;

    const desktops = this._desktops.length
      ? `<ul>${this._desktops.map((desktop) => this._desktopRow(desktop)).join('')}</ul>`
      : `<p class="empty">No desktops yet. Install HA Desktop Widget on a computer and choose Connect with Home Assistant.</p>`;

    aside.innerHTML = `
      <section>
        <div class="section-head">
          <h2>Profiles</h2>
          <button class="action" data-action="new-profile" ${this._creating ? 'disabled' : ''}>New profile</button>
        </div>
        ${createForm}
        ${profiles}
      </section>
      <section>
        <h2>Desktops</h2>
        ${desktops}
      </section>
    `;
  }

  _desktopRow(desktop) {
    const selected = this._selection?.type === 'desktop' && this._selection.id === desktop.desktop_id;
    const supportsProfiles = (desktop.capabilities || []).includes('apply_profile');
    let status = '';
    if (!supportsProfiles) {
      status = `<span class="chip">Update the app to use profiles</span>`;
    } else if (desktop.assigned_profile_id && desktop.profile_out_of_date) {
      status = `<span class="chip warn">${desktop.online ? 'Updating' : 'Updates when online'}</span>`;
    } else if (desktop.assigned_profile_id) {
      status = `<span class="chip ok">Up to date</span>`;
    }
    const options = this._profiles
      .map(
        (profile) =>
          `<option value="${escapeHtml(profile.profile_id)}" ${
            profile.profile_id === desktop.assigned_profile_id ? 'selected' : ''
          }>${escapeHtml(profile.name)}</option>`
      )
      .join('');
    const meta = [
      desktop.online ? 'Online' : 'Offline',
      titleCase(desktop.platform),
      desktop.app_version && desktop.app_version !== 'unknown' ? `v${desktop.app_version}` : '',
    ]
      .filter(Boolean)
      .join(' · ');
    const nameMarkup = `<span class="name"><span class="dot ${desktop.online ? 'online' : ''}"></span>${escapeHtml(desktop.name)}</span>
        <span class="meta">${escapeHtml(meta)}${desktop.has_snapshot ? '' : ' · No layout reported yet'}</span>`;
    return `<li class="card ${selected ? 'selected' : ''}">
      ${
        desktop.has_snapshot
          ? `<button class="item" data-action="select-desktop" data-id="${escapeHtml(desktop.desktop_id)}" title="Show this desktop's current layout" ${
              this._busy ? 'disabled' : ''
            }>${nameMarkup}</button>`
          : `<div class="item" style="cursor:default">${nameMarkup}</div>`
      }
      <div class="desktop-controls">
        <label class="inline">Profile
          <select data-field="assign" data-id="${escapeHtml(desktop.desktop_id)}" ${
            supportsProfiles && !this._busy ? '' : 'disabled'
          }>
            <option value="">None</option>
            ${options}
          </select>
        </label>
        ${status}
      </div>
    </li>`;
  }

  _renderToolbar() {
    this._keepingFocus(() => this._renderToolbarContent());
  }

  _renderToolbarContent() {
    const toolbar = this.shadowRoot.querySelector('.toolbar');
    if (!toolbar) return;
    const selection = this._selection;
    toolbar.classList.toggle('hidden', !selection);
    if (!selection) {
      toolbar.innerHTML = '';
      return;
    }
    const isProfile = selection.type === 'profile';
    const profile = isProfile
      ? this._profiles.find((item) => item.profile_id === selection.id)
      : null;
    const dirty = this._isDirty();
    const ready = !!this._draft && !this._busy;
    const sizeOptions = [
      `<option value="">Default size (${DEFAULT_SIZE.width} × ${DEFAULT_SIZE.height})</option>`,
      ...this._desktops
        .filter((item) => item.window_width && item.window_height)
        .map(
          (item) =>
            `<option value="${escapeHtml(item.desktop_id)}" ${
              this._sizeSource === item.desktop_id ? 'selected' : ''
            }>${escapeHtml(item.name)} (${item.window_width} × ${item.window_height})</option>`
        ),
    ].join('');
    const entityOptions = this._editing
      ? Object.keys(this._hass?.states || {})
          .sort()
          .map((entityId) => `<option value="${escapeHtml(entityId)}"></option>`)
          .join('')
      : '';

    toolbar.innerHTML = `
      <input class="title" data-field="name" maxlength="64" aria-label="${
        isProfile ? 'Profile name' : 'New profile name'
      }" value="${escapeHtml(this._name)}">
      ${profile ? `<span class="chip">Revision ${profile.revision}</span>` : `<span class="chip">Current desktop layout</span>`}
      <span class="chip accent ${dirty ? '' : 'hidden'}" data-role="dirty">Unsaved changes</span>
      <span class="grow"></span>
      <button class="action ${this._editing ? 'pressed' : ''}" data-action="toggle-edit" ${
        ready ? '' : 'disabled'
      } aria-pressed="${this._editing}">${this._editing ? 'Done editing tiles' : 'Edit tiles'}</button>
      ${
        this._editing
          ? `<input data-field="entity" list="hadw-entities" placeholder="Add an entity, e.g. light.desk" value="${escapeHtml(
              this._entityInput
            )}" aria-label="Entity to add">
             <datalist id="hadw-entities">${entityOptions}</datalist>
             <button class="action" data-action="add-entity">Add</button>`
          : ''
      }
      <select data-field="size" aria-label="Preview size">${sizeOptions}</select>
      <button class="action" data-action="discard" ${dirty && ready ? '' : 'disabled'}>Discard</button>
      <button class="action primary" data-action="save" data-role="save" ${
        this._canSave() ? '' : 'disabled'
      }>${isProfile ? 'Save' : 'Save as profile'}</button>
      ${isProfile ? `<button class="action danger" data-action="delete" ${this._busy ? 'disabled' : ''}>Delete</button>` : ''}
    `;
  }

  _canSave() {
    if (!this._draft || this._busy || !this._name.trim()) return false;
    return this._selection?.type === 'desktop' || this._isDirty();
  }

  // Typing in the name field must not re-render the toolbar, which would steal focus.
  _updateSaveState() {
    const save = this.shadowRoot.querySelector('[data-role="save"]');
    if (save) save.disabled = !this._canSave();
    const dirty = this.shadowRoot.querySelector('[data-role="dirty"]');
    if (dirty) dirty.classList.toggle('hidden', !this._isDirty());
    const discard = this.shadowRoot.querySelector('[data-action="discard"]');
    if (discard) discard.disabled = !this._isDirty() || this._busy;
  }

  _renderBanners() {
    const banners = this.shadowRoot.querySelector('.banners');
    if (!banners) return;
    const parts = [];
    if (this._error) {
      parts.push(
        `<div class="banner error" role="alert">${escapeHtml(this._error)} <button class="action" data-action="dismiss">Dismiss</button></div>`
      );
    }
    if (this._notice) parts.push(`<div class="banner info" role="status">${escapeHtml(this._notice)}</div>`);
    if (this._selection?.type === 'desktop') {
      parts.push(
        `<div class="banner info">This is the layout the desktop last reported. Changes here are not sent to it; save them as a profile and assign the profile to apply them.</div>`
      );
    }
    banners.innerHTML = parts.join('');
  }

  _renderStage() {
    const placeholder = this.shadowRoot.querySelector('.placeholder');
    const frame = this.shadowRoot.querySelector('.frame');
    if (!placeholder || !frame) return;
    const showing = !!this._selection;
    frame.classList.toggle('hidden', !showing);
    placeholder.classList.toggle('hidden', showing);
    placeholder.innerHTML = this._profiles.length
      ? 'Choose a profile to preview and edit it, or a desktop to see its current layout.'
      : 'Create a profile to share a widget layout between desktops. Start from a connected desktop’s layout or from an empty profile.';
    if (showing) this._applySize();
  }
}

if (!customElements.get('ha-desktop-widget-panel')) {
  customElements.define('ha-desktop-widget-panel', HaDesktopWidgetPanel);
}

export { mergeEditedSections, sameJson, withPreviewDefaults };
