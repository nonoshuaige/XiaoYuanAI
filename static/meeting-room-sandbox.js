"use strict";

class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

function select(selector) {
    const element = document.querySelector(selector);
    if (!element) {
        throw new Error(`页面元素缺失：${selector}`);
    }
    return element;
}

const scheduleDate = select("#scheduleDate");
const previousDayButton = select("#previousDay");
const todayButton = select("#todayButton");
const nextDayButton = select("#nextDay");
const refreshButton = select("#refreshButton");
const retryButton = select("#retryButton");
const displayDate = select("#displayDate");
const displayWeekday = select("#displayWeekday");
const floorCount = select("#floorCount");
const roomCount = select("#roomCount");
const scheduleWindow = select("#scheduleWindow");
const loadingState = select("#loadingState");
const emptyState = select("#emptyState");
const errorState = select("#errorState");
const errorCopy = select("#errorCopy");
const floorList = select("#floorList");
const roomDialog = select("#roomDialog");
const closeDialogButton = select("#closeDialog");
const dialogFloor = select("#dialogFloor");
const dialogRoomName = select("#dialogRoomName");
const dialogRoomMeta = select("#dialogRoomMeta");
const dialogDate = select("#dialogDate");
const timeline = select("#timeline");

let latestRooms = [];
let loading = false;

function localDateValue(date) {
    const year = String(date.getFullYear());
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function apiDate(value) {
    return value.replaceAll("-", "/");
}

function parseLocalDate(value) {
    const [year, month, day] = value.split("-").map(Number);
    return new Date(year, month - 1, day);
}

function shiftDate(value, amount) {
    const date = parseLocalDate(value);
    date.setDate(date.getDate() + amount);
    return localDateValue(date);
}

function formatDate(value) {
    return new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
    }).format(parseLocalDate(value));
}

function weekdayName(value) {
    return new Intl.DateTimeFormat("zh-CN", {
        weekday: "long",
    }).format(parseLocalDate(value));
}

function formatObservedAt(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(date);
}

async function api(url) {
    const response = await fetch(url);
    const body = await response.json().catch(() => null);
    if (!response.ok) {
        let message = "请求失败，请稍后重试";
        if (body && typeof body === "object" && "detail" in body) {
            message = typeof body.detail === "string"
                ? body.detail
                : message;
        }
        throw new ApiError(response.status, message);
    }
    return body;
}

function showState(state) {
    loadingState.hidden = state !== "loading";
    floorList.hidden = state !== "rooms";
    emptyState.hidden = state !== "empty";
    errorState.hidden = state !== "error";
}

function setLoading(active) {
    loading = active;
    refreshButton.disabled = active;
    refreshButton.classList.toggle("loading", active);
}

async function loadSchedule(options = {}) {
    if (loading) {
        return;
    }
    try {
        if (!scheduleDate.value) {
            throw new Error("请选择日期");
        }
        setLoading(true);
        if (!options.silent) {
            showState("loading");
        }
        const parameters = new URLSearchParams({
            date: apiDate(scheduleDate.value),
        });
        const result = await api(
            `/api/sandbox/meeting-rooms?${parameters.toString()}`
        );
        latestRooms = result.rooms;
        renderFloors(result.rooms);
        renderSummary(result);
        showState(result.rooms.length ? "rooms" : "empty");
    }
    catch (error) {
        errorCopy.textContent = userMessage(error);
        showState("error");
    }
    finally {
        setLoading(false);
    }
}

function renderSummary(result) {
    const floors = new Set(result.rooms.map((room) => room.floor));
    displayDate.textContent = formatDate(scheduleDate.value);
    displayWeekday.textContent =
        `${weekdayName(scheduleDate.value)} · ${formatObservedAt(result.observedAt)} 更新`;
    floorCount.textContent = `${floors.size} 层`;
    roomCount.textContent = `${result.rooms.length} 间`;
    scheduleWindow.textContent = result.displayWindow.replace("-", "–");
}

function renderFloors(rooms) {
    const groups = new Map();
    rooms.forEach((room) => {
        if (!groups.has(room.floor)) {
            groups.set(room.floor, []);
        }
        groups.get(room.floor).push(room);
    });

    const fragment = document.createDocumentFragment();
    [...groups.entries()]
        .sort((first, second) => floorNumber(first[0]) - floorNumber(second[0]))
        .forEach(([floor, floorRooms]) => {
            fragment.appendChild(createFloorSection(floor, floorRooms));
        });
    floorList.replaceChildren(fragment);
}

function floorNumber(floor) {
    return Number(floor.replace("F", ""));
}

function createFloorSection(floor, rooms) {
    const section = document.createElement("section");
    section.className = "floor-section";
    section.setAttribute("aria-labelledby", `floor-${floorNumber(floor)}`);

    const heading = document.createElement("header");
    heading.className = "floor-heading";
    const headingCopy = document.createElement("div");
    const title = document.createElement("h2");
    title.id = `floor-${floorNumber(floor)}`;
    title.textContent = floor;
    const subtitle = document.createElement("p");
    subtitle.textContent = "会议室";
    headingCopy.append(title, subtitle);
    const count = document.createElement("span");
    count.textContent = `${rooms.length} 间`;
    heading.append(headingCopy, count);

    const list = document.createElement("div");
    list.className = "room-list";
    rooms.forEach((room) => {
        list.appendChild(createRoomRow(room));
    });
    section.append(heading, list);
    return section;
}

function createRoomRow(room) {
    const button = document.createElement("button");
    button.className = "room-row";
    button.type = "button";
    button.setAttribute(
        "aria-label",
        `查看${room.roomName}在${formatDate(scheduleDate.value)}的日程`
    );

    const identity = document.createElement("div");
    identity.className = "room-identity";
    const nameLine = document.createElement("div");
    nameLine.className = "room-name-line";
    const name = document.createElement("strong");
    name.textContent = room.roomName;
    const capacity = document.createElement("span");
    capacity.className = "room-capacity";
    capacity.textContent = `${room.capacity}人`;
    nameLine.append(name, capacity);
    const equipment = document.createElement("span");
    equipment.className = "room-equipment";
    equipment.textContent = room.equipment.join(" · ");
    identity.append(nameLine, equipment);

    const preview = document.createElement("div");
    preview.className = "mini-schedule";
    const track = document.createElement("div");
    track.className = "mini-track";
    room.timeline.forEach((slot) => {
        const marker = document.createElement("span");
        marker.className = `mini-slot${slot.available ? "" : " occupied"}`;
        marker.title = slot.available
            ? `${slot.timeRange} 可用`
            : `${slot.timeRange} ${slot.booking?.theme || "不可用"}`;
        track.appendChild(marker);
    });
    const axis = document.createElement("div");
    axis.className = "mini-axis";
    timelineAxisLabels(room.timeline).forEach((label) => {
        const marker = document.createElement("span");
        marker.textContent = label;
        axis.appendChild(marker);
    });
    preview.append(track, axis);

    const freeSlots = room.timeline.filter((slot) => slot.available).length;
    const status = document.createElement("div");
    status.className = "room-status";
    const statusValue = document.createElement("strong");
    statusValue.textContent = `${freeSlots * 0.5} 小时可用`;
    const statusNote = document.createElement("span");
    statusNote.textContent = room.occupied.length
        ? `${room.occupied.length} 条预约`
        : "当前展示时段无预约";
    status.append(statusValue, statusNote);

    const chevron = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "svg"
    );
    chevron.classList.add("room-chevron");
    chevron.setAttribute("viewBox", "0 0 20 20");
    chevron.setAttribute("fill", "none");
    chevron.innerHTML = '<path d="m7.5 5 5 5-5 5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>';

    button.append(identity, preview, status, chevron);
    button.addEventListener("click", () => openRoomDialog(room));
    return button;
}

function timelineAxisLabels(slots) {
    if (!slots.length) {
        return ["18:00"];
    }
    const indexes = [
        0,
        Math.floor(slots.length / 3),
        Math.floor(slots.length * 2 / 3),
    ];
    return [
        ...new Set([
            ...indexes.map((index) => slots[index].start),
            slots.at(-1).end,
        ]),
    ];
}

function openRoomDialog(room) {
    dialogFloor.textContent = room.floor;
    dialogRoomName.textContent = room.roomName;
    dialogRoomMeta.textContent =
        `容纳 ${room.capacity} 人 · ${room.equipment.join(" / ")}`;
    dialogDate.textContent =
        `${formatDate(scheduleDate.value)} · ${weekdayName(scheduleDate.value)}`;
    renderTimeline(room.timeline);
    roomDialog.showModal();
    closeDialogButton.focus();
}

function renderTimeline(slots) {
    const fragment = document.createDocumentFragment();
    slots.forEach((slot) => {
        const row = document.createElement("div");
        row.className = "timeline-row";
        const time = document.createElement("span");
        time.className = "timeline-time";
        time.textContent = slot.timeRange;
        const state = document.createElement("div");
        state.className =
            `timeline-state${slot.available ? "" : " occupied"}`;
        const stateName = document.createElement("strong");
        stateName.textContent = slot.available
            ? "可用"
            : (slot.booking?.theme || "不可用");
        const detail = document.createElement("span");
        detail.textContent = slot.available
            ? "暂无预约"
            : (slot.booking?.bookedBy || "已预约");
        state.append(stateName, detail);
        row.append(time, state);
        fragment.appendChild(row);
    });
    timeline.replaceChildren(fragment);
}

function closeRoomDialog() {
    if (roomDialog.open) {
        roomDialog.close();
    }
}

function userMessage(error) {
    if (error instanceof ApiError || error instanceof Error) {
        return error.message;
    }
    return "发生未知错误，请稍后重试";
}

previousDayButton.addEventListener("click", () => {
    scheduleDate.value = shiftDate(scheduleDate.value, -1);
    void loadSchedule();
});
nextDayButton.addEventListener("click", () => {
    scheduleDate.value = shiftDate(scheduleDate.value, 1);
    void loadSchedule();
});
todayButton.addEventListener("click", () => {
    scheduleDate.value = localDateValue(new Date());
    void loadSchedule();
});
scheduleDate.addEventListener("change", () => void loadSchedule());
refreshButton.addEventListener("click", () => void loadSchedule());
retryButton.addEventListener("click", () => void loadSchedule());
closeDialogButton.addEventListener("click", closeRoomDialog);
roomDialog.addEventListener("click", (event) => {
    if (event.target === roomDialog) {
        closeRoomDialog();
    }
});
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !roomDialog.open) {
        void loadSchedule({ silent: true });
    }
});

scheduleDate.value = localDateValue(new Date());
void loadSchedule();
window.setInterval(() => {
    if (document.visibilityState === "visible" && !roomDialog.open) {
        void loadSchedule({ silent: true });
    }
}, 30_000);
