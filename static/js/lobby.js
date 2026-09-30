/**
 * Movie Guess lobby — Public Rooms (landing) and Create Room / I Have a Code.
 * Submits the same POST /join form the backend already expects; `category`
 * carries one or more comma-separated category ids.
 * Lobby list still comes from /ws/lobby.
 */
(function () {
    const STORAGE_KEY = "movie_guess_lobby_draft";
    const COPY_FLAG = "movie_guess_copy_invite";
    const NAME_MAX = 10;
    const MAX_PLAYERS = 10;

    const root = document.getElementById("lobby-app");
    if (!root) return;

    const categories = (() => {
        try {
            return JSON.parse(root.dataset.categories || "[]");
        } catch {
            return ["hollywood_movies", "hollywood_characters"];
        }
    })();

    // Category ids are "<genre>_<type>", e.g. "bollywood_movies", "asian_dramas_characters".
    const GENRE_ORDER = ["bollywood", "hollywood", "anime", "asian_dramas", "cartoon"];
    const KIND_ORDER = ["movies", "characters"];
    // Wording from the design ("Asian Drama Character", "Hollywood Movies").
    const GENRE_LABELS = { asian_dramas: "Asian Drama" };
    const KIND_LABELS = { characters: "Character", movies: "Movies" };
    const LEGACY_CATEGORIES = { movies: "hollywood_movies", characters: "hollywood_characters" };

    function splitCategory(id) {
        const value = LEGACY_CATEGORIES[id] || String(id || "");
        const cut = value.lastIndexOf("_");
        return cut > 0
            ? { genre: value.slice(0, cut), kind: value.slice(cut + 1) }
            : { genre: value, kind: "" };
    }

    function titleize(value) {
        return String(value || "")
            .split("_")
            .filter(Boolean)
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
            .join(" ");
    }

    function categoryLabel(id) {
        const { genre, kind } = splitCategory(id);
        const genreLabel = GENRE_LABELS[genre] || titleize(genre);
        const kindLabel = kind ? (KIND_LABELS[kind] || titleize(kind)) : "";
        return `${genreLabel} ${kindLabel}`.trim().toUpperCase();
    }

    function orderIndex(list, value) {
        const i = list.indexOf(value);
        return i === -1 ? list.length : i;
    }

    // Dropdown options: one per category id, grouped by genre (design order).
    const categoryOptions = categories
        .map((id) => ({ id, ...splitCategory(id) }))
        .sort((a, b) =>
            orderIndex(GENRE_ORDER, a.genre) - orderIndex(GENRE_ORDER, b.genre)
            || a.genre.localeCompare(b.genre)
            || orderIndex(KIND_ORDER, a.kind) - orderIndex(KIND_ORDER, b.kind))
        .map((c) => ({ id: c.id, label: categoryLabel(c.id) }));

    const DEFAULT_CATEGORY = categories.includes("hollywood_movies")
        ? "hollywood_movies"
        : (categoryOptions[0] && categoryOptions[0].id) || "hollywood_movies";

    const els = {
        error: document.getElementById("error-banner"),
        screens: {
            public: document.getElementById("screen-public"),
            create: document.getElementById("screen-create"),
        },
        topBtn: document.getElementById("top-nav-btn"),
        nameInputs: Array.from(document.querySelectorAll(".js-name-input")),
        createPane: document.getElementById("create-pane"),
        codePane: document.getElementById("code-pane"),
        categorySelect: document.getElementById("category-select"),
        categoryTrigger: document.getElementById("category-trigger"),
        categoryValue: document.getElementById("category-value"),
        categoryList: document.getElementById("category-list"),
        roundsRow: document.getElementById("rounds-pills"),
        duration: document.getElementById("duration-slider"),
        durationVal: document.getElementById("duration-val"),
        publicToggle: document.getElementById("public-toggle"),
        roomCodeInput: document.getElementById("have-code-input"),
        createBtn: document.getElementById("create-copy-btn"),
        joinRoomBtn: document.getElementById("join-room-btn"),
        roomList: document.getElementById("room-list"),
        form: document.getElementById("join-form"),
        fields: {
            name: document.getElementById("field-name"),
            action: document.getElementById("field-action"),
            roomType: document.getElementById("field-room-type"),
            maxPlayers: document.getElementById("field-max-players"),
            rounds: document.getElementById("field-rounds"),
            duration: document.getElementById("field-duration"),
            category: document.getElementById("field-category"),
            roomCode: document.getElementById("field-room-code"),
            guestId: document.getElementById("field-guest-id"),
        },
    };

    const state = {
        screen: "public", // public | create
        createTab: "create", // create | code
        categories: [DEFAULT_CATEGORY],
        rounds: 3,
        duration: 60,
        publicRoom: false,
        roomCode: "",
        name: "",
        rooms: [],
    };

    function clampName(value) {
        return String(value || "").slice(0, NAME_MAX);
    }

    // Mirrors clean_player_name() in main.py (the server is the real check):
    // letters/marks/digits in any script plus space _ . ' - , at least one
    // letter or digit.
    const NAME_PATTERN = /^[\p{L}\p{M}\p{N} _.'-]+$/u;
    const NAME_HAS_LETTER = /[\p{L}\p{N}]/u;
    const INVALID_NAME_MESSAGE = "Use letters, numbers, spaces and _ . ' - only.";

    function isValidName(name) {
        return NAME_PATTERN.test(name) && NAME_HAS_LETTER.test(name);
    }

    function createGuestId() {
        if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
            return crypto.randomUUID();
        }
        if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
            const bytes = new Uint8Array(16);
            crypto.getRandomValues(bytes);
            bytes[6] = (bytes[6] & 0x0f) | 0x40;
            bytes[8] = (bytes[8] & 0x3f) | 0x80;
            const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
            return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
        }
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
            const r = (Math.random() * 16) | 0;
            return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
        });
    }

    function getOrCreateGuestId() {
        let guestId = localStorage.getItem("scribble_guest_id");
        if (!guestId) {
            guestId = createGuestId();
            localStorage.setItem("scribble_guest_id", guestId);
        }
        document.cookie = `guest_id=${guestId}; path=/; max-age=31536000; SameSite=Lax`;
        return guestId;
    }

    function saveDraft() {
        const draft = {
            screen: state.screen,
            createTab: state.createTab,
            categories: state.categories,
            rounds: state.rounds,
            duration: state.duration,
            publicRoom: state.publicRoom,
            roomCode: state.roomCode,
            name: clampName(state.name),
        };
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
        } catch (_) { /* ignore */ }
    }

    function loadDraft() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            const draft = JSON.parse(raw);
            Object.assign(state, {
                screen: ["public", "create"].includes(draft.screen) ? draft.screen : state.screen,
                createTab: draft.createTab === "code" ? "code" : "create",
                rounds: draft.rounds || state.rounds,
                duration: Math.max(30, Math.min(120, draft.duration || state.duration)),
                publicRoom: draft.publicRoom === true,
                roomCode: draft.roomCode || "",
                name: clampName(draft.name || ""),
            });
            const saved = Array.isArray(draft.categories)
                ? draft.categories.filter((id) => categories.includes(id))
                : [];
            if (saved.length) state.categories = saved;
        } catch (_) { /* ignore */ }
    }

    function showError(message) {
        els.error.textContent = message || "";
        els.error.classList.toggle("is-visible", !!message);
    }

    function setScreen(name) {
        state.screen = name;
        Object.entries(els.screens).forEach(([key, node]) => {
            node.classList.toggle("is-active", key === name);
        });
        // Design: Public list offers "Private Rooom"; Create offers "Back".
        const onPublic = name === "public";
        els.topBtn.textContent = onPublic ? "PRIVATE ROOOM" : "BACK";
        els.topBtn.dataset.action = onPublic ? "create" : "public";
        saveDraft();
        render();
    }

    function setCreateTab(tab) {
        state.createTab = tab;
        saveDraft();
        render();
    }

    /* ---------- category multi-select ---------- */

    let categoryOpen = false;
    let categoryHighlight = -1;

    function checkboxIcon(selected) {
        return `/static/img/icons/${selected ? "ic-checkbox-on" : "ic-checkbox-off"}.svg`;
    }

    // Trigger lists the picks in dropdown order, comma separated (design).
    function renderCategoryValue() {
        state.categories = state.categories.filter((id) => categories.includes(id));
        if (!state.categories.length) state.categories = [DEFAULT_CATEGORY];
        els.categoryValue.textContent = categoryOptions
            .filter((o) => state.categories.includes(o.id))
            .map((o) => o.label)
            .join(", ");
    }

    function renderCategorySelect() {
        renderCategoryValue();

        els.categoryTrigger.setAttribute("aria-expanded", String(categoryOpen));
        els.categorySelect.classList.toggle("is-open", categoryOpen);
        els.categoryList.classList.toggle("hidden", !categoryOpen);
        els.categoryList.innerHTML = "";
        if (!categoryOpen) return;

        categoryOptions.forEach((option, index) => {
            const selected = state.categories.includes(option.id);
            const item = document.createElement("li");
            item.className = "mg-select-option" + (index === categoryHighlight ? " is-highlighted" : "");
            item.id = `category-option-${index}`;
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", String(selected));

            const label = document.createElement("span");
            label.className = "mg-select-option-label";
            label.textContent = option.label;

            const box = document.createElement("span");
            box.className = "mg-check";
            box.innerHTML = `<img src="${checkboxIcon(selected)}" width="19.99" height="20" alt="">`;

            item.append(label, box);
            item.addEventListener("mouseenter", () => setCategoryHighlight(index));
            item.addEventListener("click", () => toggleCategory(option.id));
            els.categoryList.appendChild(item);
        });
        const active = els.categoryList.children[categoryHighlight];
        if (active) {
            els.categoryList.setAttribute("aria-activedescendant", active.id);
            active.scrollIntoView({ block: "nearest" });
        } else {
            els.categoryList.removeAttribute("aria-activedescendant");
        }
    }

    function setCategoryHighlight(index) {
        if (index === categoryHighlight) return;
        categoryHighlight = index;
        Array.from(els.categoryList.children).forEach((item, i) => {
            item.classList.toggle("is-highlighted", i === index);
        });
        const active = els.categoryList.children[index];
        if (active) {
            els.categoryList.setAttribute("aria-activedescendant", active.id);
            active.scrollIntoView({ block: "nearest" });
        }
    }

    function setCategoryOpen(open) {
        categoryOpen = open;
        categoryHighlight = open ? 0 : -1;
        renderCategorySelect();
        if (open) els.categoryList.focus();
    }

    // Multi-select: the list stays open; at least one category stays picked.
    // Options are updated in place so the open list (and outside-click check) stay intact.
    function toggleCategory(id) {
        if (state.categories.includes(id)) {
            if (state.categories.length === 1) return;
            state.categories = state.categories.filter((c) => c !== id);
        } else {
            state.categories = state.categories.concat(id);
        }
        saveDraft();
        renderCategoryValue();
        Array.from(els.categoryList.children).forEach((item, i) => {
            const selected = state.categories.includes(categoryOptions[i].id);
            item.setAttribute("aria-selected", String(selected));
            item.querySelector(".mg-check img").src = checkboxIcon(selected);
        });
    }

    /* ---------- render ---------- */

    function setSliderFill(slider) {
        const min = Number(slider.min) || 0;
        const max = Number(slider.max) || 100;
        const pct = max > min ? ((Number(slider.value) - min) / (max - min)) * 100 : 0;
        slider.style.setProperty("--fill", `${pct}%`);
    }

    function renderRounds() {
        els.roundsRow.innerHTML = "";
        [1, 3, 5].forEach((n) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "pill" + (state.rounds === n ? " is-active" : "");
            btn.textContent = String(n);
            btn.addEventListener("click", () => {
                state.rounds = n;
                saveDraft();
                render();
            });
            els.roundsRow.appendChild(btn);
        });
    }

    function renderNameInputs() {
        state.name = clampName(state.name);
        els.nameInputs.forEach((input) => {
            if (input.value !== state.name) input.value = state.name;
            const count = input.closest(".name-field").querySelector(".js-name-count");
            if (count) count.textContent = `${state.name.length}/${NAME_MAX}`;
        });
    }

    function roomCategories(room) {
        const list = Array.isArray(room.categories) && room.categories.length
            ? room.categories
            : [room.category || DEFAULT_CATEGORY];
        return list.map(categoryLabel).join(", ");
    }

    function renderRooms() {
        const list = els.roomList;
        list.innerHTML = "";
        if (!state.rooms.length) {
            list.innerHTML = `<p class="empty-state">No public rooms available right now. Create one!</p>`;
            return;
        }
        state.rooms.forEach((room) => {
            const full = room.count >= room.max;
            const row = document.createElement("div");
            row.className = "room-row";

            const info = document.createElement("div");
            info.className = "room-info";
            info.innerHTML = `
                <div class="room-id"></div>
                <div class="room-details">
                    <div class="room-meta">
                        <span class="room-players">
                            <span class="room-icon"><img src="/static/img/icons/ic-users.svg" width="20.0409" height="19.9999" alt=""></span>
                            <span class="room-count"></span>
                        </span>
                        <span aria-hidden="true">|</span>
                        <span class="room-rounds"></span>
                    </div>
                    <p class="room-categories"></p>
                </div>
            `;
            info.querySelector(".room-id").textContent = room.room_id;
            if (room.in_progress) {
                const tag = document.createElement("span");
                tag.className = "room-live-tag";
                tag.textContent = "IN PROGRESS";
                info.querySelector(".room-id").appendChild(tag);
            }
            info.querySelector(".room-count").textContent = `${room.count}/${room.max}`;
            info.querySelector(".room-rounds").textContent = `${room.rounds || 3} ROUNDS`;
            info.querySelector(".room-categories").textContent = roomCategories(room);

            const join = document.createElement("button");
            join.type = "button";
            join.className = "join-btn";
            join.textContent = "JOIN";
            join.disabled = full;
            join.addEventListener("click", () => joinPublicRoom(room));

            row.append(info, join);
            list.appendChild(row);
        });
    }

    function render() {
        const onCode = state.createTab === "code";
        els.createPane.classList.toggle("hidden", onCode);
        els.codePane.classList.toggle("hidden", !onCode);
        els.createBtn.classList.toggle("hidden", onCode);
        els.joinRoomBtn.classList.toggle("hidden", !onCode);
        document.querySelectorAll(".mode-tab").forEach((tab) => {
            tab.classList.toggle("is-active", tab.dataset.tab === state.createTab);
        });

        els.duration.value = state.duration;
        setSliderFill(els.duration);
        els.durationVal.textContent = `${state.duration} SEC`;

        els.publicToggle.checked = state.publicRoom;

        if (els.roomCodeInput.value !== state.roomCode) els.roomCodeInput.value = state.roomCode;

        renderNameInputs();
        renderCategorySelect();
        renderRounds();
        if (state.screen === "public") renderRooms();
    }

    /* ---------- submit ---------- */

    // Validates the shared name field on the current screen. Returns the name,
    // "" when it was left blank (the server then picks a funny name from the room's
    // categories), or null when the typed name isn't allowed.
    function requireName() {
        const input = els.screens[state.screen].querySelector(".js-name-input");
        const name = clampName(state.name).trim();
        if (name && !isValidName(name)) {
            showError(INVALID_NAME_MESSAGE);
            if (input) input.focus();
            return null;
        }
        showError("");
        return name;
    }

    function submitJoin({ action, name, roomCode = "", roomType = "private", copyLink = false }) {
        state.name = name;
        state.roomCode = roomCode;
        els.fields.name.value = name;
        els.fields.guestId.value = getOrCreateGuestId();
        els.fields.action.value = action;
        els.fields.roomType.value = roomType;
        els.fields.maxPlayers.value = String(MAX_PLAYERS);
        els.fields.rounds.value = String(state.rounds);
        els.fields.duration.value = String(state.duration);
        els.fields.category.value = state.categories.join(",");
        els.fields.roomCode.value = roomCode;

        try {
            // Don't pre-save the typed name: /join may rename a duplicate
            // (e.g. "Sam" -> "Sam(1)"). The game page picks up the server-assigned
            // name from the join cookie and stores it for this tab's refreshes.
            sessionStorage.removeItem("movie_guess_player_name");
            // A fresh /join issues a new session token; drop any old per-tab copies
            // so the game page picks the new one up from the cookie.
            Object.keys(sessionStorage)
                .filter((key) => key.startsWith("movie_guess_player_token:"))
                .forEach((key) => sessionStorage.removeItem(key));
            sessionStorage.setItem("movie_guess_player_room", roomCode);
            if (copyLink) sessionStorage.setItem(COPY_FLAG, "1");
            // Keep the name/settings for when the user comes back to the lobby.
            saveDraft();
        } catch (_) { /* ignore */ }
        els.form.submit();
    }

    function createRoom() {
        const name = requireName();
        if (name === null) return;
        submitJoin({
            action: "create",
            name,
            roomType: state.publicRoom ? "public" : "private",
            copyLink: true,
        });
    }

    function joinWithCode() {
        const name = requireName();
        if (name === null) return;
        const code = extractRoomCode(els.roomCodeInput.value);
        if (!code) {
            showError("Please enter a room code.");
            els.roomCodeInput.focus();
            return;
        }
        submitJoin({ action: "join", name, roomCode: code });
    }

    function joinPublicRoom(room) {
        const name = requireName();
        if (name === null) return;
        submitJoin({ action: "join", name, roomCode: room.room_id, roomType: "public" });
    }

    function parseQuery() {
        const params = new URLSearchParams(window.location.search);
        const error = params.get("error");
        const invite = params.get("invite");

        if (error) {
            const messages = {
                name_taken: "Username already exists in this room. Try another name.",
                not_found: "Room not found. Check your code.",
                missing_code: "Please enter a room code.",
                banned: "You have been banned from this room and cannot rejoin.",
                full: "This room is full. Try another room.",
                ended: "This room has ended. Create or join another room.",
                room_expired: "No one joined within 5 minutes, so your public room was closed.",
                rejoin: "Your session for this room has ended. Enter your name to join again.",
                invalid_name: "That name can't be used. Use letters, numbers, spaces and _ . ' - only (max 10).",
            };
            showError(messages[error] || "Something went wrong.");
            if (error === "not_found" || error === "missing_code") {
                state.screen = "create";
                state.createTab = "code";
            }
        }

        if (invite) {
            try {
                state.roomCode = atob(invite).toUpperCase();
                // Design: join with code is name + code on the "I have a code" tab
                state.screen = "create";
                state.createTab = "code";
            } catch (_) {
                showError("Invalid or corrupted invite link.");
            }
        }
    }

    function extractRoomCode(value) {
        const raw = String(value || "").trim();
        if (!raw) return "";
        if (/https?:\/\//i.test(raw) || /[?&]invite=/i.test(raw)) {
            try {
                const urlMatch = raw.match(/https?:\/\/[^\s]+/i) || raw.match(/\S+/);
                const invite = urlMatch ? new URL(urlMatch[0], window.location.origin).searchParams.get("invite") : null;
                if (invite) {
                    return atob(invite).toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
                }
            } catch (_) { /* ignore malformed URLs */ }
            return "";
        }
        return raw.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
    }

    function connectLobby() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        let retryMs = 1200;

        const connect = () => {
            const ws = new WebSocket(`${protocol}://${window.location.host}/ws/lobby`);
            ws.onmessage = (event) => {
                let data;
                try {
                    data = JSON.parse(event.data);
                } catch {
                    return;
                }
                if (data.type === "lobby_update" && Array.isArray(data.rooms)) {
                    state.rooms = data.rooms;
                    if (state.screen === "public") renderRooms();
                }
            };
            ws.onclose = () => {
                setTimeout(connect, retryMs);
                retryMs = Math.min(retryMs * 1.5, 8000);
            };
            ws.onopen = () => {
                retryMs = 1200;
            };
        };
        connect();
    }

    /* ---------- events ---------- */

    document.querySelectorAll(".mode-tab").forEach((tab) => {
        tab.addEventListener("click", () => setCreateTab(tab.dataset.tab));
    });

    els.topBtn.addEventListener("click", () => {
        showError("");
        setScreen(els.topBtn.dataset.action === "create" ? "create" : "public");
    });

    els.nameInputs.forEach((input) => {
        input.addEventListener("input", () => {
            state.name = clampName(input.value);
            renderNameInputs();
            saveDraft();
        });
        input.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" || state.screen !== "create") return;
            event.preventDefault();
            if (state.createTab === "code") joinWithCode(); else createRoom();
        });
    });

    els.categoryTrigger.addEventListener("click", () => setCategoryOpen(!categoryOpen));

    els.categoryTrigger.addEventListener("keydown", (event) => {
        if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            setCategoryOpen(true);
        }
    });

    els.categoryList.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setCategoryHighlight(Math.min(categoryOptions.length - 1, categoryHighlight + 1));
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setCategoryHighlight(Math.max(0, categoryHighlight - 1));
        } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            const option = categoryOptions[categoryHighlight];
            if (option) toggleCategory(option.id);
        } else if (event.key === "Escape" || event.key === "Tab") {
            if (event.key === "Escape") event.preventDefault();
            setCategoryOpen(false);
            if (event.key === "Escape") els.categoryTrigger.focus();
        }
    });

    document.addEventListener("click", (event) => {
        if (categoryOpen && !els.categorySelect.contains(event.target)) setCategoryOpen(false);
    });

    els.duration.addEventListener("input", () => {
        state.duration = Math.max(30, Math.min(120, Number(els.duration.value) || 60));
        saveDraft();
        render();
    });

    els.publicToggle.addEventListener("change", () => {
        state.publicRoom = els.publicToggle.checked;
        saveDraft();
        render();
    });

    els.roomCodeInput.addEventListener("input", () => {
        state.roomCode = els.roomCodeInput.value.toUpperCase();
        saveDraft();
    });

    els.roomCodeInput.addEventListener("paste", (event) => {
        const pasted = (event.clipboardData && (event.clipboardData.getData("text/plain") || event.clipboardData.getData("text"))) || "";
        const extracted = extractRoomCode(pasted);
        if (!extracted) return;
        event.preventDefault();
        els.roomCodeInput.value = extracted;
        state.roomCode = extracted;
        saveDraft();
    });

    els.roomCodeInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            joinWithCode();
        }
    });

    els.createBtn.addEventListener("click", createRoom);
    els.joinRoomBtn.addEventListener("click", joinWithCode);

    // Boot. Guest-id setup must not block pills — HTTP LAN is not a secure context.
    try {
        getOrCreateGuestId();
    } catch (_) { /* ignore */ }
    loadDraft();
    parseQuery();
    setScreen(state.screen);
    try {
        connectLobby();
    } catch (_) { /* ignore */ }
})();
