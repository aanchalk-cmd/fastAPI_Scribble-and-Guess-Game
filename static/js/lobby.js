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
            return ["movies", "characters"];
        }
    })();

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
        categoryRow: document.getElementById("category-pills"),
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
        category: categories.includes("movies") ? "movies" : (categories[0] || "movies"),
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
            category: state.category,
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
                category: draft.category || state.category,
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
        if (state.category === "mix") {
            const pool = ["movies", "characters"].filter((c) => categories.includes(c));
            if (pool.length) {
                return pool[Math.floor(Math.random() * pool.length)];
            }
        }
        if (categories.includes(state.category)) return state.category;
        return categories.includes("movies") ? "movies" : (categories[0] || "movies");
    }

    function renderCategoryPills() {
        // Design: Movies, Characters, Mix (Mix = random of available real categories)
        const preferred = ["movies", "characters"];
        const ordered = preferred.filter((c) => categories.includes(c));
        const list = ordered.length ? ordered : categories.slice(0, 2);
        els.categoryRow.innerHTML = "";
        list.forEach((cat) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "pill" + (state.category === cat ? " is-active" : "");
            btn.textContent = cat.toUpperCase();
            btn.addEventListener("click", () => {
                state.category = cat;
                saveDraft();
                render();
            });
            els.categoryRow.appendChild(btn);
        });
        if (list.length >= 2) {
            const mixBtn = document.createElement("button");
            mixBtn.type = "button";
            mixBtn.className = "pill" + (state.category === "mix" ? " is-active" : "");
            mixBtn.textContent = "MIX";
            mixBtn.addEventListener("click", () => {
                state.category = "mix";
                saveDraft();
                render();
            });
            els.categoryRow.appendChild(mixBtn);
        }
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
            const cat = (room.category || "movies").toUpperCase();
            const rounds = room.rounds || 3;
            row.innerHTML = `
                <div>
                    <div class="room-id">${room.room_id}</div>
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
                    category: room.category || "movies",
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
        els.durationVal.textContent = `${state.duration} MIN`;
        els.publicToggle.checked = state.publicRoom;
        els.maxPlayers.value = state.maxPlayers;
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
                    <span>${(room.category || "movies").toUpperCase()}</span>
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

        renderCategoryPills();
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
            sessionStorage.setItem("movie_guess_player_name", state.name);
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
