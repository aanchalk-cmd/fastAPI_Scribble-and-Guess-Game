/**
 * Movie Guess lobby — Create Room / Public Rooms / Name screens.
 * Submits the same POST /join form the backend already expects.
 * Lobby list still comes from /ws/lobby.
 */
(function () {
    const STORAGE_KEY = "movie_guess_lobby_draft";
    const COPY_FLAG = "movie_guess_copy_invite";

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
        roomList: document.getElementById("room-list"),
        nameInput: document.getElementById("player-name"),
        nameTitle: document.getElementById("name-screen-label"),
        nameSubmit: document.getElementById("name-submit-btn"),
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
        rooms: [],
    };

    function getOrCreateGuestId() {
        let guestId = localStorage.getItem("scribble_guest_id");
        if (!guestId) {
            guestId = crypto.randomUUID();
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
            name: state.name,
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
                duration: draft.duration || state.duration,
                publicRoom: draft.publicRoom === true,
                maxPlayers: draft.maxPlayers || state.maxPlayers,
                roomCode: draft.roomCode || "",
                pendingAction: draft.pendingAction || "create",
                copyLink: !!draft.copyLink,
                name: draft.name || "",
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

    function renderCategoryPills() {
        // Design shows Movies + Characters only on create screen
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
        if (els.nameInput) {
            els.nameInput.value = state.name;
        }

        if (state.pendingAction === "join") {
            els.nameTitle.textContent = "NAME";
            els.nameSubmit.textContent = "ENTER ROOM";
        } else {
            els.nameTitle.textContent = "NAME";
            els.nameSubmit.textContent = state.copyLink ? "CREATE & ENTER" : "ENTER ROOM";
        }

        renderCategoryPills();
        renderRounds();
        if (state.screen === "public") renderRooms();
    }

    function syncForm() {
        const name = (els.nameInput.value || "").trim();
        state.name = name;
        els.fields.name.value = name;
        els.fields.guestId.value = getOrCreateGuestId();
        els.fields.action.value = state.pendingAction;
        els.fields.roomType.value = state.publicRoom ? "public" : "private";
        els.fields.maxPlayers.value = String(state.maxPlayers);
        els.fields.rounds.value = String(state.rounds);
        els.fields.duration.value = String(state.duration);
        els.fields.category.value = state.category;
        els.fields.roomCode.value = (state.roomCode || "").toUpperCase().trim();

        if (state.pendingAction === "create") {
            // Private create still uses room_type private
            els.fields.roomType.value = state.publicRoom ? "public" : "private";
        } else {
            // Join: room_type is ignored by backend for lookup, but keep public if known
            els.fields.roomType.value = "public";
        }
    }

    function goCreateWithCopy() {
        state.pendingAction = "create";
        state.copyLink = true;
        state.roomCode = "";
        state.cameFromPublic = false;
        setScreen("name");
    }

    function goHaveCodeContinue() {
        const code = (els.roomCodeInput.value || "").trim().toUpperCase();
        if (!code) {
            showError("Please enter a room code.");
            return;
        }
        showError("");
        state.roomCode = code;
        state.pendingAction = "join";
        state.copyLink = false;
        state.cameFromPublic = false;
        setScreen("name");
    }

    function submitName(event) {
        event.preventDefault();
        const name = (els.nameInput.value || "").trim();
        if (!name) {
            showError("Please enter your name.");
            els.nameInput.focus();
            return;
        }
        if (state.pendingAction === "join" && !(state.roomCode || "").trim()) {
            showError("Missing room code.");
            setScreen("create");
            setCreateTab("code");
            return;
        }
        showError("");
        state.name = name;
        syncForm();
        if (state.pendingAction === "create" && state.copyLink) {
            try {
                sessionStorage.setItem(COPY_FLAG, "1");
            } catch (_) { /* ignore */ }
        }
        // Clear draft after successful submit intent so refresh on /game won't reopen name screen oddly
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch (_) { /* ignore */ }
        els.form.submit();
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
            } else if (error === "name_taken" || error === "banned" || error === "full") {
                state.screen = "name";
                state.pendingAction = "join";
            }
        }

        if (invite) {
            try {
                const decoded = atob(invite).toUpperCase();
                state.roomCode = decoded;
                state.pendingAction = "join";
                state.copyLink = false;
                state.screen = "name";
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
                // Soft reconnect — refresh-friendly, does not touch game sockets
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
        state.duration = Number(els.duration.value) || 5;
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

    els.nameInput.addEventListener("input", () => {
        state.name = els.nameInput.value;
        saveDraft();
    });

    document.getElementById("create-copy-btn").addEventListener("click", goCreateWithCopy);
    document.getElementById("have-code-continue").addEventListener("click", goHaveCodeContinue);
    els.form.addEventListener("submit", submitName);

    // Boot
    getOrCreateGuestId();
    loadDraft();
    parseQuery();
    setScreen(state.screen || "create");
    connectLobby();
})();
