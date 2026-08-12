// Shared playlist-picker behavior used by the Playlist Filter, Duplicate
// Finder, and Playlist Cleanup pages: fetching + caching the user's
// playlists, and the searchable combobox (and multi-row variant of it) used
// to pick them.

let _playlistsCache = null;

async function fetchPlaylists(apiUrl, loginUrl) {
    if (_playlistsCache) return _playlistsCache;
    const resp = await fetch(apiUrl);
    if (resp.status === 401) {
        window.location = loginUrl;
        return [];
    }
    _playlistsCache = await resp.json();
    return _playlistsCache;
}

// A small searchable combobox: text input + filtered dropdown panel showing
// each playlist's cover art.
function setupCombo(input, panel, initial) {
    const state = {
        id: initial ? initial.id : null,
        name: initial ? initial.name : null,
        filtered: [],
        highlighted: -1,
    };
    input.value = state.name || "";

    function renderPanel() {
        panel.innerHTML = "";
        if (state.filtered.length === 0) {
            const empty = document.createElement("div");
            empty.className = "combo-empty";
            empty.textContent = "No playlists match.";
            panel.appendChild(empty);
            return;
        }
        state.filtered.forEach((p, idx) => {
            const opt = document.createElement("div");
            opt.className =
                "combo-option" + (idx === state.highlighted ? " highlighted" : "");
            if (p.image_url) {
                const img = document.createElement("img");
                img.className = "combo-thumb";
                img.src = p.image_url;
                img.alt = "";
                opt.appendChild(img);
            } else {
                const placeholder = document.createElement("div");
                placeholder.className = "combo-thumb placeholder";
                placeholder.textContent = "🎵";
                opt.appendChild(placeholder);
            }
            const label = document.createElement("span");
            label.textContent = p.name;
            opt.appendChild(label);
            opt.addEventListener("mousedown", (e) => {
                e.preventDefault();
                select(p);
            });
            panel.appendChild(opt);
        });
    }

    function filter(query) {
        const all = _playlistsCache || [];
        const q = query.trim().toLowerCase();
        state.filtered = q ? all.filter((p) => p.name.toLowerCase().includes(q)) : all;
        state.highlighted = -1;
        renderPanel();
    }

    function open() {
        panel.style.display = "block";
        filter(input.value === (state.name || "") ? "" : input.value);
    }

    function close() {
        panel.style.display = "none";
    }

    function select(p) {
        state.id = p.id;
        state.name = p.name;
        input.value = p.name;
        close();
    }

    input.addEventListener("focus", () => {
        input.select();
        open();
    });
    input.addEventListener("input", () => {
        filter(input.value);
        panel.style.display = "block";
    });
    input.addEventListener("blur", () => {
        setTimeout(() => {
            close();
            input.value = state.name || "";
        }, 100);
    });
    input.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") {
            e.preventDefault();
            if (panel.style.display !== "block") {
                open();
                return;
            }
            state.highlighted = Math.min(state.highlighted + 1, state.filtered.length - 1);
            renderPanel();
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            state.highlighted = Math.max(state.highlighted - 1, 0);
            renderPanel();
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (state.highlighted >= 0 && state.filtered[state.highlighted]) {
                select(state.filtered[state.highlighted]);
            }
        } else if (e.key === "Escape") {
            close();
            input.value = state.name || "";
            input.blur();
        }
    });

    return {
        getValue: () => ({ id: state.id, name: state.name }),
        setValue: (id, name) => {
            state.id = id;
            state.name = name;
            input.value = name || "";
        },
    };
}

// Manages a variable-length list of combo rows (e.g. "pick two or more
// playlists"), each with its own remove button. Used wherever more than one
// playlist can be picked.
function createComboRowManager(containerEl, minRows) {
    let rows = [];

    function updateRemoveButtons() {
        const disable = rows.length <= minRows;
        rows.forEach((entry) => {
            entry.row.querySelector(".remove-slot-btn").disabled = disable;
        });
    }

    function addRow(initial) {
        const row = document.createElement("div");
        row.className = "combo-row";

        const comboDiv = document.createElement("div");
        comboDiv.className = "combo";
        const input = document.createElement("input");
        input.type = "text";
        input.className = "combo-input";
        input.placeholder = "Search your playlists…";
        input.autocomplete = "off";
        const panel = document.createElement("div");
        panel.className = "combo-panel";
        comboDiv.appendChild(input);
        comboDiv.appendChild(panel);

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "button secondary remove-slot-btn";
        removeBtn.textContent = "×";

        row.appendChild(comboDiv);
        row.appendChild(removeBtn);
        containerEl.appendChild(row);

        const combo = setupCombo(input, panel, initial);
        const entry = { row, getValue: combo.getValue };
        removeBtn.addEventListener("click", () => removeRow(entry));
        rows.push(entry);
        updateRemoveButtons();
    }

    function removeRow(entry) {
        if (rows.length <= minRows) return;
        rows = rows.filter((e) => e !== entry);
        entry.row.remove();
        updateRemoveButtons();
    }

    function reset(initialValues) {
        containerEl.innerHTML = "";
        rows = [];
        initialValues.forEach((v) => addRow(v));
    }

    return {
        addRow,
        reset,
        getValues: () => rows.map((e) => e.getValue()),
    };
}
