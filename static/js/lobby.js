/**
 * Movie Guess lobby — Create Room / Public Rooms / Name screens.
 * Submits the same POST /join form the backend already expects.
 * Lobby list still comes from /ws/lobby.
 */
(function () {
    const STORAGE_KEY = "movie_guess_lobby_draft";
    const COPY_FLAG = "movie_guess_copy_invite";
    const NAME_MAX = 10;

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
    const KIND_ORDER = ["characters", "movies"];
    // Dropdown wording from the design ("Asian Drama Character", "Hollywood Movies").
    const GENRE_OPTION_LABELS = { asian_dramas: "Asian Drama" };
    const KIND_OPTION_LABELS = { characters: "Character", movies: "Movies", mix: "Mix" };
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
        return kind ? `${titleize(genre)} ${titleize(kind)}` : titleize(genre);
    }

    const genres = (() => {
        const found = [];
        categories.forEach((id) => {
            const { genre } = splitCategory(id);
            if (genre && !found.includes(genre)) found.push(genre);
        });
        const ordered = GENRE_ORDER.filter((g) => found.includes(g));
        return ordered.concat(found.filter((g) => !ordered.includes(g)));
    })();

    function kindsFor(genre) {
        const found = categories
            .map(splitCategory)
            .filter((c) => c.genre === genre && c.kind)
            .map((c) => c.kind);
        const ordered = KIND_ORDER.filter((k) => found.includes(k));
        return ordered.concat(found.filter((k) => !ordered.includes(k)));
    }

    const els = {
        error: document.getElementById("error-banner"),
        screens: {
            create: document.getElementById("screen-create"),
            public: document.getElementById("screen-public"),
            name: document.getElementById("screen-name"),
        },
        topBtn: document.getElementById("top-nav-btn"),
        brand: document.getElementById("brand-title"),
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
        maxPlayersWrap: document.getElementById("max-players-wrap"),
        maxPlayers: document.getElementById("max-players-slider"),
        maxPlayersVal: document.getElementById("max-players-val"),
        roomCodeInput: document.getElementById("have-code-input"),
        joinNameInput: document.getElementById("join-name-input"),
        joinRoomBtn: document.getElementById("join-room-btn"),
        roomList: document.getElementById("room-list"),
        nameInput: document.getElementById("player-name"),
        nameTitle: document.getElementById("name-screen-label"),
        nameSubmit: document.getElementById("name-submit-btn"),
        nameCount: document.getElementById("player-name-count"),
        joinNameCount: document.getElementById("join-name-count"),
        nameFieldError: document.getElementById("name-field-error"),
        publicJoinHeader: document.getElementById("public-join-header"),
        joinRoomId: document.getElementById("join-room-id"),
        joinRoomMeta: document.getElementById("join-room-meta"),
        copyJoinCode: document.getElementById("copy-join-code"),
        nameScreen: document.getElementById("screen-name"),
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
        screen: "create",
        createTab: "create", // create | code
        genre: genres.includes("hollywood") ? "hollywood" : (genres[0] || "hollywood"),
        kind: "movies", // movies | characters | mix
        rounds: 3,
        duration: 5,
        publicRoom: false,
        maxPlayers: 4,
        roomCode: "",
        pendingAction: "create", // create | join
        copyLink: false,
        name: "",
        cameFromPublic: false,
        selectedRoom: null,
        rooms: [],
    };

    function clampName(value) {
        return String(value || "").slice(0, NAME_MAX);
    }

    function updateCharCount(input, countEl) {
        if (!input || !countEl) return;
        const len = (input.value || "").length;
        countEl.textContent = `${len}/${NAME_MAX}`;
    }

    function setNameFieldError(visible) {
        if (!els.nameFieldError) return;
        els.nameFieldError.classList.toggle("hidden", !visible);
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
            genre: state.genre,
            kind: state.kind,
            rounds: state.rounds,
            duration: state.duration,
            publicRoom: state.publicRoom,
            maxPlayers: state.maxPlayers,
            roomCode: state.roomCode,
            pendingAction: state.pendingAction,
            copyLink: state.copyLink,
            name: clampName(state.name),
            cameFromPublic: !!state.cameFromPublic,
            selectedRoom: state.selectedRoom,
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
                screen: draft.screen || state.screen,
                createTab: draft.createTab || state.createTab,
                rounds: draft.rounds || state.rounds,
                duration: Math.max(2, Math.min(5, draft.duration || state.duration)),
                publicRoom: draft.publicRoom === true,
                maxPlayers: draft.maxPlayers || state.maxPlayers,
                roomCode: draft.roomCode || "",
                pendingAction: draft.pendingAction || "create",
                copyLink: !!draft.copyLink,
            name: clampName(draft.name || ""),
            cameFromPublic: !!draft.cameFromPublic,
            selectedRoom: draft.selectedRoom || null,
        });
            // Older drafts stored a single flat category ("movies" / "characters" / "mix").
            if (draft.genre && genres.includes(draft.genre)) state.genre = draft.genre;
            if (draft.kind) {
                state.kind = draft.kind;
            } else if (["movies", "characters", "mix"].includes(draft.category)) {
                state.kind = draft.category;
            }
        } catch (_) { /* ignore */ }
    }

    function showError(message) {
        if (!message) {
            els.error.classList.remove("is-visible");
            els.error.textContent = "";
            return;
        }
        els.error.textContent = message;
        els.error.classList.add("is-visible");
    }

    function setScreen(name) {
        state.screen = name;
        Object.entries(els.screens).forEach(([key, node]) => {
            node.classList.toggle("is-active", key === name);
        });

        if (name === "public") {
            els.topBtn.textContent = "BACK";
            els.topBtn.dataset.action = "back";
        } else if (name === "name") {
            els.topBtn.textContent = "BACK";
            els.topBtn.dataset.action = "back-name";
        } else {
            els.topBtn.textContent = "PUBLIC ROOMS";
            els.topBtn.dataset.action = "public";
        }
        saveDraft();
        render();
    }

    function setCreateTab(tab) {
        state.createTab = tab;
        saveDraft();
        render();
    }

    function resolveCategoryForSubmit() {
        const kinds = kindsFor(state.genre);
        let kind = state.kind;
        if (kind === "mix" || !kinds.includes(kind)) {
            kind = kind === "mix" && kinds.length
                ? kinds[Math.floor(Math.random() * kinds.length)]
                : (kinds[0] || "movies");
        }
        const id = `${state.genre}_${kind}`;
        if (categories.includes(id)) return id;
        return categories.includes("hollywood_movies") ? "hollywood_movies" : (categories[0] || "hollywood_movies");
    }

    // One dropdown entry per genre + type, plus "<Genre> Mix" (random of the two) when a genre has both.
    const categoryOptions = (() => {
        const options = [];
        genres.forEach((genre) => {
            const kinds = kindsFor(genre);
            const withMix = kinds.length >= 2 ? kinds.concat("mix") : kinds;
            withMix.forEach((kind) => {
                const genreLabel = GENRE_OPTION_LABELS[genre] || titleize(genre);
                const kindLabel = KIND_OPTION_LABELS[kind] || titleize(kind);
                options.push({ genre, kind, label: `${genreLabel} ${kindLabel}`.toUpperCase() });
            });
        });
        return options;
    })();

    let categoryOpen = false;
    let categoryHighlight = -1;

    function isSelectedOption(option) {
        return option.genre === state.genre && option.kind === state.kind;
    }

    // Options shown in the open list: everything except the current choice (design).
    function visibleCategoryOptions() {
        return categoryOptions.filter((option) => !isSelectedOption(option));
    }

    function renderCategorySelect() {
        if (!categoryOptions.some(isSelectedOption)) {
            const fallback = categoryOptions.find((o) => o.genre === state.genre) || categoryOptions[0];
            if (fallback) {
                state.genre = fallback.genre;
                state.kind = fallback.kind;
            }
        }
        const selected = categoryOptions.find(isSelectedOption);
        els.categoryValue.textContent = selected ? selected.label : "";

        els.categoryTrigger.setAttribute("aria-expanded", String(categoryOpen));
        els.categorySelect.classList.toggle("is-open", categoryOpen);
        els.categoryList.classList.toggle("hidden", !categoryOpen);
        els.categoryList.innerHTML = "";
        if (!categoryOpen) return;

        visibleCategoryOptions().forEach((option, index) => {
            const item = document.createElement("li");
            item.className = "mg-select-option" + (index === categoryHighlight ? " is-highlighted" : "");
            item.id = `category-option-${index}`;
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", "false");
            item.textContent = option.label;
            item.addEventListener("mouseenter", () => setCategoryHighlight(index));
            item.addEventListener("click", () => chooseCategory(option));
            els.categoryList.appendChild(item);
        });
        const active = els.categoryList.querySelector(".is-highlighted");
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
        els.categoryList.querySelectorAll(".mg-select-option").forEach((item, i) => {
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

    function chooseCategory(option) {
        state.genre = option.genre;
        state.kind = option.kind;
        categoryOpen = false;
        categoryHighlight = -1;
        saveDraft();
        render();
        els.categoryTrigger.focus();
    }

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
            btn.className = "pill pill-round" + (state.rounds === n ? " is-active" : "");
            btn.textContent = String(n);
            btn.addEventListener("click", () => {
                state.rounds = n;
                saveDraft();
                render();
            });
            els.roundsRow.appendChild(btn);
        });
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
            const cat = categoryLabel(room.category || "hollywood_movies").toUpperCase();
            const rounds = room.rounds || 3;
            const liveTag = room.in_progress ? `<span class="room-live-tag">IN PROGRESS</span>` : "";
            row.innerHTML = `
                <div>
                    <div class="room-id">${room.room_id}${liveTag}</div>
                    <div class="room-meta">
                        <span>👤 ${room.count}/${room.max}</span>
                        <span>${cat}</span>
                        <span>${rounds} ROUNDS</span>
                    </div>
                </div>
            `;
            const join = document.createElement("button");
            join.type = "button";
            join.className = "join-btn";
            join.textContent = "JOIN";
            join.disabled = full;
            join.addEventListener("click", () => {
                state.pendingAction = "join";
                state.roomCode = room.room_id;
                state.copyLink = false;
                state.createTab = "code";
                state.cameFromPublic = true;
                state.selectedRoom = {
                    room_id: room.room_id,
                    count: room.count,
                    max: room.max,
                    category: room.category || "hollywood_movies",
                    rounds: room.rounds || 3,
                };
                setNameFieldError(false);
                setScreen("name");
            });
            row.appendChild(join);
            list.appendChild(row);
        });
    }

    function render() {
        els.createPane.classList.toggle("hidden", state.createTab !== "create");
        els.codePane.classList.toggle("hidden", state.createTab !== "code");

        document.querySelectorAll(".mode-tab").forEach((tab) => {
            tab.classList.toggle("is-active", tab.dataset.tab === state.createTab);
        });

        els.duration.value = state.duration;
        setSliderFill(els.duration);
        els.durationVal.textContent = `${state.duration} MIN`;
        els.publicToggle.checked = state.publicRoom;
        els.maxPlayers.value = state.maxPlayers;
        setSliderFill(els.maxPlayers);
        els.maxPlayersVal.textContent = String(state.maxPlayers);
        els.maxPlayersWrap.classList.toggle("hidden", !state.publicRoom && false);
        // Max players still useful for private rooms — keep visible.

        if (els.roomCodeInput) {
            els.roomCodeInput.value = state.roomCode;
        }
        state.name = clampName(state.name);
        if (els.nameInput) {
            els.nameInput.value = state.name;
            els.nameInput.maxLength = NAME_MAX;
            updateCharCount(els.nameInput, els.nameCount);
        }
        if (els.joinNameInput) {
            els.joinNameInput.value = state.name;
            els.joinNameInput.maxLength = NAME_MAX;
            updateCharCount(els.joinNameInput, els.joinNameCount);
        }

        const publicJoin = state.cameFromPublic && state.pendingAction === "join";
        if (els.publicJoinHeader) {
            els.publicJoinHeader.classList.toggle("hidden", !publicJoin);
        }
        if (els.nameScreen) {
            els.nameScreen.classList.toggle("is-public-join", publicJoin);
        }
        if (publicJoin && state.selectedRoom) {
            const room = state.selectedRoom;
            if (els.joinRoomId) els.joinRoomId.textContent = room.room_id;
            if (els.joinRoomMeta) {
                els.joinRoomMeta.innerHTML = `
                    <span>👤 ${room.count}/${room.max}</span>
                    <span>${categoryLabel(room.category || "hollywood_movies").toUpperCase()}</span>
                    <span>${room.rounds || 3} ROUNDS</span>
                `;
            }
        }

        els.nameTitle.textContent = "NAME";
        if (publicJoin) {
            els.nameSubmit.textContent = "JOIN ROOM";
            els.nameSubmit.classList.add("btn-yellow");
            els.nameSubmit.classList.remove("btn-primary");
        } else if (state.pendingAction === "join") {
            els.nameSubmit.textContent = "ENTER ROOM";
            els.nameSubmit.classList.add("btn-primary");
            els.nameSubmit.classList.remove("btn-yellow");
        } else {
            els.nameSubmit.textContent = "CREATE & ENTER";
            els.nameSubmit.classList.add("btn-primary");
            els.nameSubmit.classList.remove("btn-yellow");
        }

        renderCategorySelect();
        renderRounds();
        if (state.screen === "public") renderRooms();
    }

    function syncForm(nameOverride, codeOverride) {
        const name = clampName(nameOverride != null ? nameOverride : (els.nameInput.value || "")).trim();
        const code = (codeOverride != null ? codeOverride : state.roomCode || "").toUpperCase().trim();
        state.name = name;
        state.roomCode = code;
        els.fields.name.value = name;
        els.fields.guestId.value = getOrCreateGuestId();
        els.fields.action.value = state.pendingAction;
        els.fields.maxPlayers.value = String(state.maxPlayers);
        els.fields.rounds.value = String(state.rounds);
        els.fields.duration.value = String(state.duration);
        els.fields.category.value = resolveCategoryForSubmit();
        els.fields.roomCode.value = code;

        if (state.pendingAction === "create") {
            els.fields.roomType.value = state.publicRoom ? "public" : "private";
        } else {
            els.fields.roomType.value = state.cameFromPublic ? "public" : "private";
        }
    }

    function finishSubmit() {
        try {
            // Don't pre-save the typed name: /join may rename a duplicate
            // (e.g. "Sam" -> "Sam(1)"). The game page picks up the server-assigned
            // name from the join cookie and stores it for this tab's refreshes.
            sessionStorage.removeItem("movie_guess_player_name");
            sessionStorage.setItem("movie_guess_player_room", state.roomCode);
        } catch (_) { /* ignore */ }
        if (state.pendingAction === "create" && state.copyLink) {
            try {
                sessionStorage.setItem(COPY_FLAG, "1");
            } catch (_) { /* ignore */ }
        }
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (_) { /* ignore */ }
        els.form.submit();
    }

    function goCreateWithCopy() {
        state.pendingAction = "create";
        state.copyLink = true;
        state.roomCode = "";
        state.cameFromPublic = false;
        state.selectedRoom = null;
        setNameFieldError(false);
        setScreen("name");
    }

    function submitJoinWithCode() {
        const name = clampName(els.joinNameInput.value || "").trim();
        const code = (els.roomCodeInput.value || "").trim().toUpperCase();
        if (!name) {
            showError("Please enter your name.");
            els.joinNameInput.focus();
            return;
        }
        if (!code) {
            showError("Please enter a room code.");
            els.roomCodeInput.focus();
            return;
        }
        showError("");
        state.pendingAction = "join";
        state.copyLink = false;
        state.cameFromPublic = false;
        state.selectedRoom = null;
        state.name = name;
        state.roomCode = code;
        syncForm(name, code);
        finishSubmit();
    }

    function submitName(event) {
        event.preventDefault();
        const name = clampName(els.nameInput.value || "").trim();
        if (!name) {
            setNameFieldError(true);
            showError("");
            els.nameInput.focus();
            return;
        }
        setNameFieldError(false);
        if (state.pendingAction === "join" && !(state.roomCode || "").trim()) {
            showError("Missing room code.");
            setScreen("create");
            setCreateTab("code");
            return;
        }
        showError("");
        state.name = name;
        syncForm(name, state.roomCode);
        finishSubmit();
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
            };
            showError(messages[error] || "Something went wrong.");
            if (error === "not_found" || error === "missing_code") {
                state.screen = "create";
                state.createTab = "code";
                state.pendingAction = "join";
            } else if (error === "name_taken" || error === "banned" || error === "full") {
                // Prefer code tab (name + code) when rejoining with a code
                if (state.roomCode) {
                    state.screen = "create";
                    state.createTab = "code";
                } else {
                    state.screen = "name";
                }
                state.pendingAction = "join";
            }
        }

        if (invite) {
            try {
                const decoded = atob(invite).toUpperCase();
                state.roomCode = decoded;
                state.pendingAction = "join";
                state.copyLink = false;
                // Design: join with code is name + code on the same tab
                state.screen = "create";
                state.createTab = "code";
            } catch (_) {
                showError("Invalid or corrupted invite link.");
            }
        }
    }

    function connectLobby() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        let ws;
        let retryMs = 1200;

        const connect = () => {
            ws = new WebSocket(`${protocol}://${window.location.host}/ws/lobby`);
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

    // Events
    document.querySelectorAll(".mode-tab").forEach((tab) => {
        tab.addEventListener("click", () => setCreateTab(tab.dataset.tab));
    });

    els.topBtn.addEventListener("click", () => {
        const action = els.topBtn.dataset.action;
        if (action === "public") {
            setScreen("public");
        } else if (action === "back-name") {
            setScreen(state.cameFromPublic ? "public" : "create");
        } else {
            setScreen("create");
        }
    });

    els.categoryTrigger.addEventListener("click", () => setCategoryOpen(!categoryOpen));

    els.categoryTrigger.addEventListener("keydown", (event) => {
        if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            setCategoryOpen(true);
        }
    });

    els.categoryList.addEventListener("keydown", (event) => {
        const options = visibleCategoryOptions();
        if (event.key === "ArrowDown") {
            event.preventDefault();
            setCategoryHighlight(Math.min(options.length - 1, categoryHighlight + 1));
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setCategoryHighlight(Math.max(0, categoryHighlight - 1));
        } else if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (options[categoryHighlight]) chooseCategory(options[categoryHighlight]);
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
        state.duration = Math.max(2, Math.min(5, Number(els.duration.value) || 5));
        saveDraft();
        render();
    });

    els.maxPlayers.addEventListener("input", () => {
        state.maxPlayers = Number(els.maxPlayers.value) || 4;
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

    els.joinNameInput.addEventListener("input", () => {
        els.joinNameInput.value = clampName(els.joinNameInput.value);
        state.name = els.joinNameInput.value;
        updateCharCount(els.joinNameInput, els.joinNameCount);
        saveDraft();
    });

    els.nameInput.addEventListener("input", () => {
        els.nameInput.value = clampName(els.nameInput.value);
        state.name = els.nameInput.value;
        updateCharCount(els.nameInput, els.nameCount);
        if (els.nameInput.value.trim()) setNameFieldError(false);
        saveDraft();
    });

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

    function copyText(value) {
        const text = String(value || "");
        const fallback = () => {
            const ta = document.createElement("textarea");
            ta.value = text;
            ta.setAttribute("readonly", "");
            ta.style.position = "absolute";
            ta.style.left = "-9999px";
            ta.style.top = `${window.pageYOffset || 0}px`;
            ta.style.userSelect = "text";
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            ta.setSelectionRange(0, text.length);
            const ok = document.execCommand("copy");
            ta.remove();
            return ok;
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text).catch(() => {
                if (!fallback()) throw new Error("copy failed");
            });
        }
        return fallback() ? Promise.resolve() : Promise.reject(new Error("copy failed"));
    }

    if (els.copyJoinCode) {
        els.copyJoinCode.addEventListener("click", (event) => {
            event.preventDefault();
            const code = extractRoomCode(state.roomCode || (state.selectedRoom && state.selectedRoom.room_id) || "");
            if (!code) return;
            copyText(code).then(() => {
                showError("");
                els.copyJoinCode.textContent = "✓";
                setTimeout(() => { els.copyJoinCode.textContent = "⧉"; }, 1200);
            }).catch(() => {});
        });
    }

    if (els.roomCodeInput) {
        els.roomCodeInput.addEventListener("paste", (event) => {
            const pasted = (event.clipboardData && (event.clipboardData.getData("text/plain") || event.clipboardData.getData("text"))) || "";
            const extracted = extractRoomCode(pasted);
            if (!extracted) return;
            event.preventDefault();
            els.roomCodeInput.value = extracted;
            state.roomCode = extracted;
            saveDraft();
        });
    }

    document.getElementById("create-copy-btn").addEventListener("click", goCreateWithCopy);
    els.joinRoomBtn.addEventListener("click", submitJoinWithCode);
    els.form.addEventListener("submit", submitName);

    // Boot. Guest-id setup must not block pills — HTTP LAN is not a secure context.
    try {
        getOrCreateGuestId();
    } catch (_) { /* ignore */ }
    loadDraft();
    parseQuery();
    setScreen(state.screen || "create");
    try {
        connectLobby();
    } catch (_) { /* ignore */ }
})();
