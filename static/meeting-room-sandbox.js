"use strict";
class ApiError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}
function select(selector) {
    const element = document.querySelector(selector);
    if (!element)
        throw new Error(`页面元素缺失：${selector}`);
    return element;
}
const floorSwitcher = select("#floorSwitcher");
const filterDate = select("#filterDate");
const filterStart = select("#filterStart");
const filterEnd = select("#filterEnd");
const filterCapacity = select("#filterCapacity");
const clearFiltersButton = select("#clearFiltersButton");
const refreshButton = select("#refreshButton");
const retryButton = select("#retryButton");
const connectionStatus = select("#connectionStatus");
const observedAt = select("#observedAt");
const summaryFloor = select("#summaryFloor");
const summaryAvailableLabel = select("#summaryAvailableLabel");
const summaryAvailable = select("#summaryAvailable");
const summaryOccupied = select("#summaryOccupied");
const queryDescription = select("#queryDescription");
const loadingState = select("#loadingState");
const emptyState = select("#emptyState");
const errorState = select("#errorState");
const errorCopy = select("#errorCopy");
const roomList = select("#roomList");
const bookingDialog = select("#bookingDialog");
const bookingForm = select("#bookingForm");
const closeDialogButton = select("#closeDialog");
const cancelBookingButton = select("#cancelBooking");
const confirmBookingButton = select("#confirmBooking");
const dialogFloor = select("#dialogFloor");
const dialogRoomName = select("#dialogRoomName");
const dialogRoomMeta = select("#dialogRoomMeta");
const bookingTheme = select("#bookingTheme");
const bookingDate = select("#bookingDate");
const bookingCapacity = select("#bookingCapacity");
const bookingStart = select("#bookingStart");
const bookingEnd = select("#bookingEnd");
const bookingConfirmed = select("#bookingConfirmed");
const bookingError = select("#bookingError");
const receiptEmpty = select("#receiptEmpty");
const receipt = select("#receipt");
const receiptRoom = select("#receiptRoom");
const receiptSlot = select("#receiptSlot");
const receiptBookingId = select("#receiptBookingId");
const receiptMeetingId = select("#receiptMeetingId");
const toast = select("#toast");
let selectedFloor = "7";
let selectedRoom = null;
let latestRooms = [];
let loading = false;
let showingAllRooms = false;
let toastTimer = null;
function localDateValue(date) {
    const year = String(date.getFullYear());
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}
function apiDate(value) {
    return value.replaceAll("-", "/");
}
function displayDate(value) {
    return value.replaceAll("/", ".");
}
function filterTimeRange() {
    if (!filterStart.value || !filterEnd.value) {
        throw new Error("请选择完整的开始和结束时间");
    }
    if (filterStart.value >= filterEnd.value) {
        throw new Error("结束时间必须晚于开始时间");
    }
    return `${filterStart.value}-${filterEnd.value}`;
}
function filterPeople() {
    if (!filterCapacity.value.trim())
        return 5;
    const value = Number(filterCapacity.value);
    if (!Number.isInteger(value) || value < 1) {
        throw new Error("参会人数必须大于0");
    }
    return value;
}
async function api(url, options = {}) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => null);
    if (!response.ok) {
        let message = "请求失败，请稍后重试";
        if (body && typeof body === "object" && "detail" in body) {
            if (typeof body.detail === "string") {
                message = body.detail;
            }
            else if (Array.isArray(body.detail)) {
                message = body.detail
                    .map((item) => item.msg || "参数不正确")
                    .join("；");
            }
        }
        throw new ApiError(response.status, message);
    }
    return body;
}
function showState(state) {
    loadingState.hidden = state !== "loading";
    roomList.hidden = state !== "rooms";
    emptyState.hidden = state !== "empty";
    errorState.hidden = state !== "error";
}
function setLoading(active) {
    loading = active;
    clearFiltersButton.disabled = active;
    refreshButton.disabled = active;
    refreshButton.classList.toggle("loading", active);
}
async function loadRooms(options = {}) {
    if (loading)
        return;
    try {
        const date = filterDate.value;
        const hasOptionalFilters = Boolean(date
            || filterStart.value
            || filterEnd.value
            || filterCapacity.value.trim());
        let timeRange = null;
        let capacity = null;
        if (hasOptionalFilters) {
            if (!date)
                throw new Error("请选择查询日期，或清空全部条件");
            timeRange = filterTimeRange();
            capacity = filterPeople();
        }
        showingAllRooms = !hasOptionalFilters;
        setLoading(true);
        if (!options.silent)
            showState("loading");
        const parameters = new URLSearchParams({ floor: selectedFloor });
        if (date && timeRange) {
            parameters.set("date", apiDate(date));
            parameters.set("timeRange", timeRange);
            parameters.set("capacity", String(capacity));
        }
        const result = await api(`/api/sandbox/meeting-rooms?${parameters.toString()}`);
        latestRooms = result.rooms;
        renderRooms(result.rooms);
        summaryFloor.textContent = `${selectedFloor}F`;
        summaryAvailableLabel.textContent =
            showingAllRooms ? "会议室总数" : "符合时段";
        summaryAvailable.textContent = String(showingAllRooms
            ? result.rooms.length
            : result.rooms.filter((room) => room.available).length);
        summaryOccupied.textContent = String(result.rooms.filter((room) => room.currentStatus === "occupied").length);
        queryDescription.textContent = showingAllRooms
            ? `${selectedFloor}F · 全部会议室 · 展示今日日程`
            : `${displayDate(result.date)} · ${timeRange} · ${capacity}人`;
        connectionStatus.textContent = "沙箱数据库已连接";
        observedAt.textContent = `服务端回读于 ${formatObservedAt(result.observedAt)}`;
        showState(result.rooms.length ? "rooms" : "empty");
    }
    catch (error) {
        errorCopy.textContent = userMessage(error);
        connectionStatus.textContent = "沙箱连接异常";
        observedAt.textContent = "未取得服务端状态";
        showState("error");
    }
    finally {
        setLoading(false);
    }
}
function renderRooms(rooms) {
    const fragment = document.createDocumentFragment();
    rooms.forEach((room, index) => {
        fragment.appendChild(createRoomCard(room, index));
    });
    roomList.replaceChildren(fragment);
}
function createRoomCard(room, index) {
    const article = document.createElement("article");
    article.className = `room-card${room.available ? "" : " unavailable"}`;
    article.style.setProperty("--index", String(index));
    const identity = document.createElement("div");
    identity.className = "room-identity";
    const titleLine = document.createElement("div");
    titleLine.className = "room-title-line";
    const title = document.createElement("h3");
    title.textContent = room.roomName;
    const status = document.createElement("span");
    status.className =
        `status-pill${room.currentStatus === "occupied" ? " occupied" : ""}`;
    status.textContent =
        room.currentStatus === "occupied" ? "当前使用中" : "当前空闲";
    titleLine.append(title, status);
    const meta = document.createElement("div");
    meta.className = "room-meta";
    const floor = document.createElement("strong");
    floor.textContent = room.floor;
    const capacity = document.createElement("span");
    capacity.textContent = `最多 ${room.capacity} 人`;
    meta.append(floor, capacity);
    const equipment = document.createElement("div");
    equipment.className = "equipment-list";
    room.equipment.forEach((name) => {
        const tag = document.createElement("span");
        tag.textContent = name;
        equipment.appendChild(tag);
    });
    identity.append(titleLine, meta, equipment);
    const schedule = document.createElement("div");
    schedule.className = "schedule";
    const scheduleHeader = document.createElement("div");
    scheduleHeader.className = "schedule-header";
    const scheduleLabel = document.createElement("strong");
    scheduleLabel.textContent = "当日日程";
    const availability = document.createElement("span");
    availability.textContent = room.available
        ? (showingAllRooms ? `${room.occupied.length} 条今日日程` : "所选时段可预约")
        : "所选时段不可用";
    scheduleHeader.append(scheduleLabel, availability);
    const scheduleItems = document.createElement("div");
    scheduleItems.className = "schedule-items";
    if (room.occupied.length === 0) {
        const empty = document.createElement("span");
        empty.className = "schedule-empty";
        empty.textContent = "全天暂无预约";
        scheduleItems.appendChild(empty);
    }
    else {
        room.occupied.forEach((booking) => {
            scheduleItems.appendChild(createBookingChip(booking));
        });
    }
    schedule.append(scheduleHeader, scheduleItems);
    const button = document.createElement("button");
    button.className = "book-button";
    button.type = "button";
    button.disabled = !room.available;
    button.textContent = room.available
        ? (showingAllRooms ? "选择并预约" : "预约这个房间")
        : "时段已占用";
    button.addEventListener("click", () => openBookingDialog(room));
    article.append(identity, schedule, button);
    return article;
}
function createBookingChip(booking) {
    const chip = document.createElement("span");
    chip.className =
        `booking-chip${booking.source === "interactive" ? " interactive" : ""}`;
    const theme = document.createElement("strong");
    theme.textContent = booking.theme;
    const time = document.createElement("small");
    time.textContent = booking.timeRange;
    chip.append(theme, time);
    chip.title = `${booking.theme} · ${booking.bookedBy}`;
    return chip;
}
function openBookingDialog(room) {
    selectedRoom = room;
    dialogFloor.textContent = room.floor;
    dialogRoomName.textContent = room.roomName;
    dialogRoomMeta.textContent =
        `容纳 ${room.capacity} 人 · ${room.equipment.join(" / ")}`;
    bookingTheme.value = "";
    bookingDate.value = filterDate.value || localDateValue(new Date());
    bookingCapacity.value = filterCapacity.value || "5";
    bookingCapacity.max = String(room.capacity);
    bookingStart.value = filterStart.value || "09:00";
    bookingEnd.value = filterEnd.value || "10:00";
    bookingConfirmed.checked = false;
    confirmBookingButton.disabled = true;
    bookingError.textContent = "";
    bookingDialog.showModal();
    window.setTimeout(() => bookingTheme.focus(), 0);
}
function closeBookingDialog() {
    if (!bookingDialog.open)
        return;
    bookingDialog.close();
    selectedRoom = null;
}
async function submitBooking(event) {
    event.preventDefault();
    if (!selectedRoom || !bookingConfirmed.checked)
        return;
    try {
        if (!bookingDate.value)
            throw new Error("请选择预约日期");
        if (!bookingStart.value || !bookingEnd.value) {
            throw new Error("请选择完整的预约时间");
        }
        if (bookingStart.value >= bookingEnd.value) {
            throw new Error("结束时间必须晚于开始时间");
        }
        const capacity = Number(bookingCapacity.value);
        if (!Number.isInteger(capacity) || capacity < 1) {
            throw new Error("参会人数必须大于0");
        }
        if (capacity > selectedRoom.capacity) {
            throw new Error(`该会议室最多容纳${selectedRoom.capacity}人`);
        }
        bookingError.textContent = "";
        confirmBookingButton.disabled = true;
        confirmBookingButton.textContent = "正在写入沙箱…";
        const result = await api("/api/sandbox/meeting-room-bookings", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                roomId: selectedRoom.roomId,
                floor: selectedRoom.floor.replace("F", ""),
                date: apiDate(bookingDate.value),
                timeRange: `${bookingStart.value}-${bookingEnd.value}`,
                confirmed: true,
                capacity,
                theme: bookingTheme.value.trim(),
            }),
        });
        renderReceipt(result);
        closeBookingDialog();
        showToast(`${result.roomName} 已写入预约，凭证已生成`);
        filterDate.value = bookingDate.value;
        filterStart.value = bookingStart.value;
        filterEnd.value = bookingEnd.value;
        filterCapacity.value = String(capacity);
        await loadRooms();
    }
    catch (error) {
        bookingError.textContent = userMessage(error);
    }
    finally {
        confirmBookingButton.textContent = "确认并写入预约";
        confirmBookingButton.disabled = !bookingConfirmed.checked;
    }
}
function renderReceipt(result) {
    receiptEmpty.hidden = true;
    receipt.hidden = false;
    receiptRoom.textContent = `${result.roomName} · ${result.theme}`;
    receiptSlot.textContent =
        `${displayDate(result.date)} · ${result.timeRange}`;
    receiptBookingId.textContent = result.bookingId;
    receiptMeetingId.textContent = result.meetingId;
}
function showToast(message) {
    if (toastTimer !== null)
        window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => {
        toast.hidden = true;
        toastTimer = null;
    }, 4200);
}
function formatObservedAt(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime()))
        return value;
    return new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).format(date);
}
function userMessage(error) {
    if (error instanceof ApiError || error instanceof Error) {
        return error.message;
    }
    return "发生未知错误，请稍后重试";
}
function selectFloor(floor) {
    selectedFloor = floor;
    floorSwitcher.querySelectorAll("button").forEach((button) => {
        const active = button.dataset.floor === floor;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    });
    void loadRooms();
}
floorSwitcher.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-floor]");
    if (button?.dataset.floor)
        selectFloor(button.dataset.floor);
});
refreshButton.addEventListener("click", () => void loadRooms());
clearFiltersButton.addEventListener("click", () => {
    filterDate.value = "";
    filterStart.value = "";
    filterEnd.value = "";
    filterCapacity.value = "";
    void loadRooms();
});
retryButton.addEventListener("click", () => void loadRooms());
[filterDate, filterStart, filterEnd, filterCapacity].forEach((input) => {
    input.addEventListener("change", () => void loadRooms());
});
bookingConfirmed.addEventListener("change", () => {
    confirmBookingButton.disabled = !bookingConfirmed.checked;
});
bookingForm.addEventListener("submit", submitBooking);
closeDialogButton.addEventListener("click", closeBookingDialog);
cancelBookingButton.addEventListener("click", closeBookingDialog);
bookingDialog.addEventListener("click", (event) => {
    if (event.target === bookingDialog)
        closeBookingDialog();
});
document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && !bookingDialog.open) {
        void loadRooms({ silent: true });
    }
});
filterDate.value = localDateValue(new Date());
floorSwitcher.querySelectorAll("button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.floor === selectedFloor));
});
void loadRooms();
window.setInterval(() => {
    if (document.visibilityState === "visible" && !bookingDialog.open) {
        void loadRooms({ silent: true });
    }
}, 20_000);
