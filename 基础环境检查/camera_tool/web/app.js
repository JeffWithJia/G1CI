(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const state = {
    devices: [],
    device: null,
    formats: [],
    controls: [],
    view: "full",
    streaming: false,
    restartTimer: null,
    writeTimers: new Map(),
  };

  const labels = {
    brightness: "亮度",
    contrast: "对比度",
    saturation: "饱和度",
    hue: "色相",
    white_balance_automatic: "自动白平衡",
    white_balance_temperature: "白平衡色温",
    gamma: "Gamma",
    gain: "增益",
    power_line_frequency: "防闪烁频率",
    sharpness: "锐度",
    backlight_compensation: "背光补偿",
    auto_exposure: "曝光模式",
    exposure_time_absolute: "曝光时间",
    exposure_dynamic_framerate: "动态帧率",
  };

  const groups = {
    "User Controls": "画面控制",
    "Camera Controls": "相机控制",
    "Other Controls": "其他控制",
  };

  const roleLabels = {
    head: "头部",
    wrist_left: "左腕",
    wrist_right: "右腕",
  };

  function icons(root = document) {
    if (window.lucide) window.lucide.createIcons({ root });
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) throw new Error(payload?.error || `${response.status} ${response.statusText}`);
    return payload;
  }

  function toast(message, type = "success") {
    const node = document.createElement("div");
    node.className = `toast ${type === "error" ? "error" : ""}`;
    node.innerHTML = `<i data-lucide="${type === "error" ? "circle-alert" : "circle-check"}"></i><span></span>`;
    node.querySelector("span").textContent = message;
    $("#toastRegion").append(node);
    icons(node);
    window.setTimeout(() => node.remove(), 3200);
  }

  function setStatus(text, mode = "offline") {
    $("#statusText").textContent = text;
    $("#statusDot").className = `status-dot ${mode}`;
  }

  function showPlaceholder(title, message) {
    $("#streamPlaceholder strong").textContent = title;
    $("#streamMessage").textContent = message;
    $("#streamPlaceholder").classList.remove("hidden");
  }

  function preferredDevice(devices) {
    const saved = localStorage.getItem("camera-device");
    return devices.find((item) => item.path === saved)
      || devices.find((item) => /USB2\.0 Camera RGB/i.test(item.name))
      || devices[0]
      || null;
  }

  async function loadDevices() {
    setStatus("正在扫描设备", "offline");
    $("#refreshDevices").classList.add("spinning");
    try {
      const payload = await api("/api/devices");
      state.devices = payload.devices;
      const select = $("#deviceSelect");
      select.replaceChildren();

      if (!state.devices.length) {
        select.add(new Option("未发现图像设备", ""));
        state.device = null;
        state.controls = [];
        renderControls();
        stopStream();
        showPlaceholder("未发现摄像头", "连接设备后点击刷新");
        setStatus("无可用设备", "offline");
        return;
      }

      state.devices.forEach((device) => {
        const role = roleLabels[device.role];
        const label = role ? `${role} · ${device.name} · ${device.stablePath}` : `${device.name} · ${device.path}`;
        select.add(new Option(label, device.path));
      });
      const selected = preferredDevice(state.devices);
      select.value = selected.path;
      await loadDevice(selected.path);
    } catch (error) {
      setStatus("服务连接失败", "offline");
      showPlaceholder("无法连接服务", error.message);
      toast(error.message, "error");
    } finally {
      $("#refreshDevices").classList.remove("spinning");
    }
  }

  async function loadDevice(devicePath) {
    if (!devicePath) return;
    stopStream();
    setStatus("正在读取参数", "offline");
    try {
      const payload = await api(`/api/device?device=${encodeURIComponent(devicePath)}`);
      state.device = payload.device;
      state.formats = payload.formats;
      state.controls = payload.controls;
      state.view = "full";
      const viewSelect = $("#viewSelect");
      viewSelect.hidden = Boolean(state.device.role);
      viewSelect.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item.dataset.view === "full"));
      localStorage.setItem("camera-device", state.device.path);
      $("#deviceName").textContent = state.device.name;
      $("#devicePath").textContent = state.device.stablePath || state.device.path;
      $("#sizeSelect").replaceChildren();
      $("#fpsSelect").replaceChildren();
      configureFormats();
      renderControls();
      showPlaceholder("预览尚未开始", "点击播放按钮打开视频流");
      setStatus("参数已读取", "offline");
      updateStreamButtons();
    } catch (error) {
      state.device = null;
      state.controls = [];
      renderControls();
      showPlaceholder("设备不可用", error.message);
      setStatus("设备读取失败", "offline");
      toast(error.message, "error");
    }
  }

  function selectedFormat() {
    return state.formats.find((format) => format.ffmpeg === $("#formatSelect").value) || state.formats[0];
  }

  function configureFormats() {
    const formatSelect = $("#formatSelect");
    formatSelect.replaceChildren();
    state.formats.forEach((format) => {
      formatSelect.add(new Option(`${format.fourcc}`, format.ffmpeg));
    });
    const mjpeg = state.formats.find((format) => format.fourcc === "MJPG");
    if (mjpeg) formatSelect.value = mjpeg.ffmpeg;
    configureSizes();
  }

  function preferredSize() {
    return state.device?.role?.startsWith("wrist_") ? "640x480" : "1280x720";
  }

  function configureSizes() {
    const format = selectedFormat();
    const sizeSelect = $("#sizeSelect");
    const previous = sizeSelect.value;
    sizeSelect.replaceChildren();
    (format?.sizes || []).forEach((size) => {
      const value = `${size.width}x${size.height}`;
      sizeSelect.add(new Option(value, value));
    });
    const available = [...sizeSelect.options].map((option) => option.value);
    const preferred = preferredSize();
    sizeSelect.value = available.includes(previous)
      ? previous
      : available.includes(preferred) ? preferred : available[0] || "";
    configureFps();
  }

  function configureFps() {
    const format = selectedFormat();
    const [width, height] = $("#sizeSelect").value.split("x").map(Number);
    const size = format?.sizes.find((item) => item.width === width && item.height === height);
    const fpsSelect = $("#fpsSelect");
    const previous = Number(fpsSelect.value);
    fpsSelect.replaceChildren();
    (size?.fps || [30]).forEach((fps) => fpsSelect.add(new Option(`${fps} fps`, String(fps))));
    const values = [...fpsSelect.options].map((option) => Number(option.value));
    fpsSelect.value = String(values.includes(previous) ? previous : values.includes(30) ? 30 : values[0] || 30);
    updateFrameBadge();
    fitVideoStage();
  }

  function fitVideoStage() {
    const wrap = $(".video-stage-wrap");
    const stage = $("#videoStage");
    if (!wrap || !stage) return;

    const [width, height] = ($("#sizeSelect").value || "16x9").split("x").map(Number);
    const ratio = width > 0 && height > 0 ? width / height : 16 / 9;
    const style = window.getComputedStyle(wrap);
    const horizontalPadding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    const verticalPadding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const availableWidth = Math.max(1, wrap.clientWidth - horizontalPadding);
    let availableHeight = Math.max(1, wrap.clientHeight - verticalPadding);
    if (window.matchMedia("(min-width: 981px)").matches) {
      const footerHeight = $(".viewer-footer")?.offsetHeight || 0;
      const viewportHeight = window.innerHeight - wrap.getBoundingClientRect().top - footerHeight - verticalPadding;
      availableHeight = Math.min(availableHeight, Math.max(1, viewportHeight));
    }

    let stageWidth = availableWidth;
    let stageHeight = stageWidth / ratio;
    if (stageHeight > availableHeight) {
      stageHeight = availableHeight;
      stageWidth = stageHeight * ratio;
    }
    stage.style.width = `${Math.floor(stageWidth)}px`;
    stage.style.height = `${Math.floor(stageHeight)}px`;
  }

  function streamUrl() {
    const params = new URLSearchParams({
      device: state.device.path,
      format: $("#formatSelect").value,
      size: $("#sizeSelect").value,
      fps: $("#fpsSelect").value,
      view: state.view,
      t: Date.now().toString(),
    });
    return `/api/stream?${params}`;
  }

  function updateStreamButtons() {
    const toggle = $("#toggleStream");
    const label = state.streaming ? "停止预览" : "开始预览";
    const icon = document.createElement("i");
    icon.setAttribute("data-lucide", state.streaming ? "square" : "play");
    toggle.replaceChildren(icon);
    toggle.title = label;
    toggle.setAttribute("aria-label", label);
    $("#restartStream").disabled = !state.streaming;
    icons(toggle);
  }

  function startStream() {
    if (!state.device || !$("#sizeSelect").value || state.streaming) return;
    state.streaming = true;
    updateStreamButtons();
    const image = $("#cameraStream");
    showPlaceholder("正在打开视频流", `${$("#sizeSelect").value} · ${$("#formatSelect").value.toUpperCase()}`);
    setStatus("正在启动预览", "offline");
    image.src = streamUrl();
    updateFrameBadge();
  }

  function stopStream(showReady = true) {
    window.clearTimeout(state.restartTimer);
    state.restartTimer = null;
    state.streaming = false;
    const image = $("#cameraStream");
    image.removeAttribute("src");
    const replacement = image.cloneNode(false);
    image.replaceWith(replacement);
    bindStreamEvents(replacement);
    updateStreamButtons();
    if (showReady && state.device) {
      showPlaceholder("预览已停止", "点击播放按钮重新打开视频流");
      setStatus("参数已读取", "offline");
    }
  }

  function restartStream() {
    if (!state.streaming) return;
    stopStream(false);
    state.restartTimer = window.setTimeout(() => {
      state.restartTimer = null;
      startStream();
    }, 160);
  }

  function restartIfStreaming() {
    if (state.streaming) restartStream();
  }

  function updateFrameBadge() {
    const size = $("#sizeSelect").value || "-";
    $("#frameBadge").textContent = `${size.replace("x", " x ")} · ${state.view.toUpperCase()}`;
  }

  function controlLabel(name) {
    return labels[name] || name.replaceAll("_", " ");
  }

  function groupedControls() {
    const result = new Map();
    state.controls.forEach((control) => {
      if (!result.has(control.group)) result.set(control.group, []);
      result.get(control.group).push(control);
    });
    return result;
  }

  function renderControls() {
    const container = $("#controlsList");
    const query = $("#controlSearch").value.trim().toLowerCase();
    container.replaceChildren();
    const visible = state.controls.filter((control) => {
      return !query || control.name.toLowerCase().includes(query) || controlLabel(control.name).toLowerCase().includes(query);
    });
    $("#controlCount").textContent = `${visible.length} 项`;

    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "empty-controls";
      empty.textContent = state.device ? "没有匹配的参数" : "请选择摄像头";
      container.append(empty);
      return;
    }

    const visibleNames = new Set(visible.map((control) => control.name));
    groupedControls().forEach((controls, groupName) => {
      const matching = controls.filter((control) => visibleNames.has(control.name));
      if (!matching.length) return;
      const section = document.createElement("section");
      section.className = "control-group";
      const heading = document.createElement("h3");
      heading.className = "group-title";
      heading.textContent = groups[groupName] || groupName;
      section.append(heading);
      matching.forEach((control) => section.append(createControl(control)));
      container.append(section);
    });
  }

  function createControl(control) {
    const row = document.createElement("div");
    const inactive = control.flags.includes("inactive") || control.flags.includes("read-only");
    row.className = `control-row${inactive ? " disabled" : ""}`;
    row.dataset.control = control.name;

    const head = document.createElement("div");
    head.className = "control-head";
    const label = document.createElement("div");
    label.className = "control-label";
    const strong = document.createElement("strong");
    strong.textContent = controlLabel(control.name);
    const code = document.createElement("code");
    code.textContent = control.name;
    label.append(strong, code);
    head.append(label);

    if (control.type === "bool") {
      const switchLabel = document.createElement("label");
      switchLabel.className = "switch";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(control.value);
      input.disabled = inactive;
      input.addEventListener("change", () => writeControl(control.name, input.checked ? 1 : 0, true));
      const track = document.createElement("span");
      track.className = "switch-track";
      switchLabel.append(input, track);
      head.append(switchLabel);
      row.append(head);
      return row;
    }

    if (control.type === "menu" || control.type === "intmenu") {
      const select = document.createElement("select");
      select.className = "control-menu";
      select.disabled = inactive;
      control.menu.forEach((item) => select.add(new Option(item.label, String(item.value))));
      select.value = String(control.value);
      select.addEventListener("change", () => writeControl(control.name, Number(select.value), true));
      head.append(select);
      row.append(head);
      return row;
    }

    const number = document.createElement("input");
    number.className = "control-number";
    number.type = "number";
    number.min = control.min;
    number.max = control.max;
    number.step = control.step || 1;
    number.value = control.value;
    number.disabled = inactive;
    head.append(number);

    const rangeLine = document.createElement("div");
    rangeLine.className = "range-line";
    const range = document.createElement("input");
    range.type = "range";
    range.min = control.min;
    range.max = control.max;
    range.step = control.step || 1;
    range.value = control.value;
    range.disabled = inactive;
    const limits = document.createElement("div");
    limits.className = "range-limits";
    limits.innerHTML = `<span>${control.min ?? ""}</span><span>${control.max ?? ""}</span>`;

    range.addEventListener("input", () => {
      number.value = range.value;
      scheduleWrite(control.name, Number(range.value));
    });
    range.addEventListener("change", () => writeControl(control.name, Number(range.value), false));
    number.addEventListener("change", () => {
      const value = Number(number.value);
      range.value = String(value);
      writeControl(control.name, value, false);
    });

    rangeLine.append(range, limits);
    row.append(head, rangeLine);
    return row;
  }

  function scheduleWrite(name, value) {
    window.clearTimeout(state.writeTimers.get(name));
    state.writeTimers.set(name, window.setTimeout(() => writeControl(name, value, false), 140));
  }

  async function writeControl(name, value, rerender) {
    if (!state.device) return;
    window.clearTimeout(state.writeTimers.get(name));
    try {
      const payload = await api("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: state.device.path, name, value }),
      });
      state.controls = payload.controls;
      if (rerender || name.includes("automatic") || name === "auto_exposure") renderControls();
    } catch (error) {
      toast(`${controlLabel(name)}：${error.message}`, "error");
      await reloadControls(false);
    }
  }

  async function reloadControls(showToast = true) {
    if (!state.device) return;
    try {
      const payload = await api(`/api/device?device=${encodeURIComponent(state.device.path)}`);
      state.controls = payload.controls;
      renderControls();
      if (showToast) toast("参数已重新读取");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function saveCurrentControls() {
    if (!state.device) return;
    try {
      await api("/api/profile/save-current", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: state.device.path }),
      });
      toast("当前参数已保存");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function applySavedControls() {
    if (!state.device) return;
    try {
      const result = await api("/api/profile/apply-saved", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: state.device.path }),
      });
      state.controls = result.controls;
      renderControls();
      const suffix = result.skipped.length ? `，跳过 ${result.skipped.length} 项` : "";
      toast(`已应用 ${result.applied.length} 项保存参数${suffix}`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function exportProfile() {
    if (!state.device) return;
    const link = document.createElement("a");
    link.href = `/api/profile/export?device=${encodeURIComponent(state.device.path)}`;
    link.download = `camera-profile-${new Date().toISOString().slice(0, 19).replaceAll(":", "-")}.json`;
    link.click();
  }

  function parseLegacyProfile(text) {
    const controls = {};
    text.split(/\r?\n/).forEach((line) => {
      const match = line.trim().match(/^([A-Za-z0-9_]+)=(-?\d+)$/);
      if (match) controls[match[1]] = Number(match[2]);
    });
    return { controls };
  }

  async function importProfile(file) {
    if (!file || !state.device) return;
    try {
      const text = await file.text();
      let profile;
      try {
        profile = JSON.parse(text);
      } catch {
        profile = parseLegacyProfile(text);
      }
      if (!profile.controls || typeof profile.controls !== "object") throw new Error("配置文件中没有 controls");
      const result = await api("/api/profile/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: state.device.path, controls: profile.controls }),
      });
      state.controls = result.controls;
      renderControls();
      const suffix = result.skipped.length ? `，跳过 ${result.skipped.length} 项` : "";
      toast(`已应用 ${result.applied.length} 项${suffix}`);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      $("#profileFile").value = "";
    }
  }

  async function restoreDefaults() {
    if (!state.device) return;
    try {
      const result = await api("/api/defaults", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: state.device.path }),
      });
      state.controls = result.controls;
      renderControls();
      toast(`已恢复 ${result.applied.length} 项默认值`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function takeSnapshot() {
    const image = $("#cameraStream");
    if (!image.naturalWidth || !image.naturalHeight) {
      toast("当前没有可抓取的视频帧", "error");
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    canvas.getContext("2d").drawImage(image, 0, 0);
    const link = document.createElement("a");
    link.download = `camera-${state.view}-${new Date().toISOString().replaceAll(":", "-")}.jpg`;
    link.href = canvas.toDataURL("image/jpeg", 0.94);
    link.click();
    toast(`已抓取 ${canvas.width} x ${canvas.height}`);
  }

  function bindStreamEvents(image) {
    image.addEventListener("load", () => {
      fitVideoStage();
      $("#streamPlaceholder").classList.add("hidden");
      setStatus("实时预览中", "online");
    });
    image.addEventListener("error", async () => {
      state.streaming = false;
      updateStreamButtons();
      let message = "检查设备占用、格式和权限";
      if (state.device) {
        try {
          const result = await api(`/api/stream/error?device=${encodeURIComponent(state.device.path)}`);
          if (result.error) message = result.error;
        } catch {
          // Keep the generic hint when the diagnostic endpoint is unavailable.
        }
      }
      showPlaceholder("视频流未启动", message);
      setStatus("预览失败", "offline");
    });
  }

  function bindEvents() {
    $("#deviceSelect").addEventListener("change", (event) => loadDevice(event.target.value));
    $("#refreshDevices").addEventListener("click", loadDevices);
    $("#reloadControls").addEventListener("click", () => reloadControls());
    $("#toggleStream").addEventListener("click", () => state.streaming ? stopStream() : startStream());
    $("#restartStream").addEventListener("click", restartStream);
    $("#snapshot").addEventListener("click", takeSnapshot);
    $("#formatSelect").addEventListener("change", () => { configureSizes(); restartIfStreaming(); });
    $("#sizeSelect").addEventListener("change", () => { configureFps(); restartIfStreaming(); });
    $("#fpsSelect").addEventListener("change", restartIfStreaming);
    $("#controlSearch").addEventListener("input", renderControls);
    $("#saveCurrent").addEventListener("click", saveCurrentControls);
    $("#applySaved").addEventListener("click", applySavedControls);
    $("#exportProfile").addEventListener("click", exportProfile);
    $("#importProfile").addEventListener("click", () => $("#profileFile").click());
    $("#profileFile").addEventListener("change", (event) => importProfile(event.target.files[0]));
    $("#copyDevicePath").addEventListener("click", async () => {
      if (!state.device) return;
      await navigator.clipboard.writeText($("#devicePath").textContent);
      toast("设备路径已复制");
    });
    $("#viewSelect").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-view]");
      if (!button) return;
      state.view = button.dataset.view;
      $("#viewSelect").querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
      restartIfStreaming();
    });
    $("#resetDefaults").addEventListener("click", () => $("#confirmDialog").showModal());
    $("#confirmDialog").addEventListener("close", () => {
      if ($("#confirmDialog").returnValue === "confirm") restoreDefaults();
    });

    bindStreamEvents($("#cameraStream"));
    window.addEventListener("resize", fitVideoStage);
    new ResizeObserver(fitVideoStage).observe($(".video-stage-wrap"));
  }

  icons();
  bindEvents();
  loadDevices();
})();
