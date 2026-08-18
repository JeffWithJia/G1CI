(function () {
  "use strict";

  const STORAGE_KEY = "robotDailyRecord.v1";
  const REMOTE_API_CONFIG_KEY = "robotRecord.remoteApi.v1";
  const REMOTE_API_DEFAULT_URL = "http://10.178.170.5:43921/api/v1/robot-record/projects/project-a";
  const REMOTE_API_KEY_PATTERN = /^[a-zA-Z0-9_-]{32,128}$/;
  const ROBOT_COUNT = 11;
  const projectData = window.RobotProjectData;
  const recordData = window.RobotRecordData;
  const problemData = window.RobotProblemData;
  const weeklyData = window.RobotWeeklyData;

  const availabilityOptions = [
    ["normal", "正常"],
    ["disabled", "停用"]
  ];

  const repairOptions = [
    ["none", "—"],
    ["diagnosing", "排查中"],
    ["repairing", "维修中"],
    ["afterSales", "待售后"],
    ["awayRepair", "离场"]
  ];

  const severityOptions = problemData.PRIORITIES;
  const faultSeverityOptions = problemData.SEVERITIES;

  const issueStatusOptions = [
    ["open", "待处理"],
    ["diagnosing", "排查中"],
    ["inProgress", "处理中"],
    ["waiting", "等待外部"],
    ["recovered", "已恢复待验证"],
    ["closed", "已关闭"]
  ];

  const defaultRecord = {
    availability: "normal",
    repairStatus: "none",
    severity: "low",
    softwareIssue: "",
    hardwareIssue: "",
    repairProgress: "",
    nextAction: "",
    owner: "",
    updatedAt: ""
  };

  const reportTheme = {
    bg: "#f5f7fa",
    surface: "#ffffff",
    surfaceSoft: "#f9fbfd",
    ink: "#202833",
    muted: "#64707d",
    line: "#d9e0e7",
    green: "#1f8a5b",
    greenSoft: "#e6f4ee",
    amber: "#b86e00",
    amberSoft: "#fff4df",
    red: "#c23b32",
    redSoft: "#fdebea",
    blue: "#2563eb",
    blueSoft: "#e9f0ff",
    graySoft: "#edf1f5"
  };

  const state = {
    portfolio: null,
    store: null,
    activeDate: toLocalDateString(new Date()),
    storagePath: "",
    storageError: "",
    saveTimer: 0,
    saveChain: Promise.resolve(),
    activeIssueId: "",
    datePickerMonth: "",
    taxonomyDraft: null,
    taxonomySystemId: "",
    taxonomyModuleId: "",
    projectApiRefreshing: false,
    toastTimer: 0
  };

  const els = {};
  const hostRequests = new Map();
  const hostReadyResolvers = [];
  let hostReady = window.parent === window;
  let hostRequestSequence = 0;

  function classificationCatalog() {
    return state.store?.classificationCatalog || problemData.defaultClassificationCatalog();
  }

  function categoryOptions() {
    return classificationCatalog().systems.map((system) => [system.id, system.label]);
  }

  function categoryLabel(value) {
    return problemData.systemLabel(value, classificationCatalog());
  }

  function categoryModuleOptions(systemCategory) {
    return problemData.moduleOptions(systemCategory, classificationCatalog());
  }

  function categoryModuleLabel(systemCategory, moduleCategory) {
    return problemData.moduleLabel(systemCategory, moduleCategory, classificationCatalog());
  }

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("message", handleHostResponse);

  function refreshIcons() {
    if (window.lucide) {
      window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
    }
  }

  async function handleHostResponse(event) {
    if (event.source !== window.parent || event.data?.channel !== "robot-record") {
      return;
    }
    if (event.data.action === "host-ready") {
      hostReady = true;
      hostReadyResolvers.splice(0).forEach((resolve) => resolve());
      return;
    }
    if (event.data.action === "data-updated") {
      await reloadStoreFromHost(event.data.sync);
      return;
    }
    if (event.data.action === "refresh-api") {
      await refreshProjectApi({ automatic: true });
      return;
    }
    const pending = hostRequests.get(event.data.id);
    if (!pending) return;
    hostRequests.delete(event.data.id);
    clearTimeout(pending.timer);
    if (event.data.ok) pending.resolve(event.data.result);
    else pending.reject(new Error(event.data.error || "项目数据操作失败。"));
  }

  function waitForHost() {
    if (hostReady) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        const index = hostReadyResolvers.indexOf(onReady);
        if (index >= 0) hostReadyResolvers.splice(index, 1);
        reject(new Error("无法连接个人工作台的数据服务。"));
      }, 8000);
      const onReady = () => {
        clearTimeout(timer);
        resolve();
      };
      hostReadyResolvers.push(onReady);
    });
  }

  async function requestHost(action, payload = {}) {
    await waitForHost();
    const id = `robot-record-${Date.now()}-${hostRequestSequence += 1}`;
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        hostRequests.delete(id);
        reject(new Error("项目数据操作超时。"));
      }, action === "remote-load" ? 20000 : 10000);
      hostRequests.set(id, { resolve, reject, timer });
      window.parent.postMessage({
        channel: "robot-record",
        id,
        action,
        ...payload
      }, "*");
    });
  }

  async function init() {
    bindElements();
    state.activeDate = els.dateInput.value || state.activeDate;
    els.dateInput.value = state.activeDate;
    bindEvents();
    state.portfolio = await loadPortfolio();
    state.store = currentProject()?.data || createDefaultStore();
    if (state.portfolio._needsMigrationSave) {
      delete state.portfolio._needsMigrationSave;
      saveStore({ immediate: true });
    }
    renderAll();
    renderStorageState();
    refreshIcons();
  }

  function bindElements() {
    els.projectName = document.getElementById("projectName");
    els.renameProjectBtn = document.getElementById("renameProjectBtn");
    els.projectApiBtn = document.getElementById("projectApiBtn");
    els.projectDialog = document.getElementById("projectDialog");
    els.projectForm = document.getElementById("projectForm");
    els.projectNameInput = document.getElementById("projectNameInput");
    els.projectApiDialog = document.getElementById("projectApiDialog");
    els.projectApiName = document.getElementById("projectApiName");
    els.projectApiStatus = document.getElementById("projectApiStatus");
    els.projectApiUrl = document.getElementById("projectApiUrl");
    els.projectApiToken = document.getElementById("projectApiToken");
    els.projectApiRemoteName = document.getElementById("projectApiRemoteName");
    els.projectApiUpdatedAt = document.getElementById("projectApiUpdatedAt");
    els.projectApiError = document.getElementById("projectApiError");
    els.toggleProjectApiTokenBtn = document.getElementById("toggleProjectApiTokenBtn");
    els.clearProjectApiBtn = document.getElementById("clearProjectApiBtn");
    els.refreshProjectApiBtn = document.getElementById("refreshProjectApiBtn");
    els.dateInput = document.getElementById("dateInput");
    els.datePickerBtn = document.getElementById("datePickerBtn");
    els.datePopover = document.getElementById("datePopover");
    els.previousCalendarMonthBtn = document.getElementById("previousCalendarMonthBtn");
    els.nextCalendarMonthBtn = document.getElementById("nextCalendarMonthBtn");
    els.datePickerMonthLabel = document.getElementById("datePickerMonthLabel");
    els.datePickerDays = document.getElementById("datePickerDays");
    els.clearDateRangeBtn = document.getElementById("clearDateRangeBtn");
    els.customDateRangeBtn = document.getElementById("customDateRangeBtn");
    els.applyTrendCustomBtn = document.getElementById("applyTrendCustomBtn");
    els.taxonomyBtn = document.getElementById("taxonomyBtn");
    els.settingsBtn = document.getElementById("settingsBtn");
    els.trendRangeSelect = document.getElementById("trendRangeSelect");
    els.trendCustomRange = document.getElementById("trendCustomRange");
    els.trendCustomStart = document.getElementById("trendCustomStart");
    els.trendCustomEnd = document.getElementById("trendCustomEnd");
    els.weeklyReportBtn = document.getElementById("weeklyReportBtn");
    els.weeklyPeriodLabel = document.getElementById("weeklyPeriodLabel");
    els.weeklyImportState = document.getElementById("weeklyImportState");
    els.problemRobotTotal = document.getElementById("problemRobotTotal");
    els.problemRobotStatus = document.getElementById("problemRobotStatus");
    els.problemIssueTotalLabel = document.getElementById("problemIssueTotalLabel");
    els.problemIssueCompare = document.getElementById("problemIssueCompare");
    els.problemTopIssue = document.getElementById("problemTopIssue");
    els.problemTopIssueMeta = document.getElementById("problemTopIssueMeta");
    els.problemAffectedRobots = document.getElementById("problemAffectedRobots");
    els.problemAffectedRatio = document.getElementById("problemAffectedRatio");
    els.weeklyIssueTotal = document.getElementById("weeklyIssueTotal");
    els.weeklyRobotTotal = document.getElementById("weeklyRobotTotal");
    els.weeklyDetailCount = document.getElementById("weeklyDetailCount");
    els.weeklyIssueChart = document.getElementById("weeklyIssueChart");
    els.weeklyRobotChart = document.getElementById("weeklyRobotChart");
    els.weeklySystemChart = document.getElementById("weeklySystemChart");
    els.weeklySystemTotal = document.getElementById("weeklySystemTotal");
    els.weeklyCompareTitle = document.getElementById("weeklyCompareTitle");
    els.weeklyCompareChart = document.getElementById("weeklyCompareChart");
    els.weeklyRobotDetails = document.getElementById("weeklyRobotDetails");
    els.problemRobotSection = document.getElementById("problemRobotSection");
    els.viewAllRobotDetailsBtn = document.getElementById("viewAllRobotDetailsBtn");
    els.scrollRobotCardsBtn = document.getElementById("scrollRobotCardsBtn");
    els.weeklyReportDialog = document.getElementById("weeklyReportDialog");
    els.weeklyReportPeriod = document.getElementById("weeklyReportPeriod");
    els.weeklyReportCanvas = document.getElementById("weeklyReportCanvas");
    els.weeklyReportText = document.getElementById("weeklyReportText");
    els.copyWeeklyReportTextBtn = document.getElementById("copyWeeklyReportTextBtn");
    els.copyWeeklyReportImageBtn = document.getElementById("copyWeeklyReportImageBtn");
    els.downloadWeeklyReportImageBtn = document.getElementById("downloadWeeklyReportImageBtn");
    els.robotTableBody = document.getElementById("robotTableBody");
    els.visibleRowsLabel = document.getElementById("visibleRowsLabel");
    els.storagePathLabel = document.getElementById("storagePathLabel");
    els.autosaveState = document.getElementById("autosaveState");
    els.availabilityRate = document.getElementById("availabilityRate");
    els.availableCount = document.getElementById("availableCount");
    els.normalCount = document.getElementById("normalCount");
    els.normalRobotCodes = document.getElementById("normalRobotCodes");
    els.disabledCount = document.getElementById("disabledCount");
    els.repairingSummary = document.getElementById("repairingSummary");
    els.afterSalesSummary = document.getElementById("afterSalesSummary");
    els.awayRepairSummary = document.getElementById("awayRepairSummary");
    els.issueRobotCount = document.getElementById("issueRobotCount");
    els.openIssueCount = document.getElementById("openIssueCount");
    els.todayIssueCount = document.getElementById("todayIssueCount");
    els.overdueIssueCount = document.getElementById("overdueIssueCount");
    els.issueQueue = document.getElementById("issueQueue");
    els.issueQueueCount = document.getElementById("issueQueueCount");
    els.newIssueBtn = document.getElementById("newIssueBtn");
    els.historyRobotSelect = document.getElementById("historyRobotSelect");
    els.historyList = document.getElementById("historyList");
    els.settingsDialog = document.getElementById("settingsDialog");
    els.robotSettingsList = document.getElementById("robotSettingsList");
    els.addRobotBtn = document.getElementById("addRobotBtn");
    els.saveRobotsBtn = document.getElementById("saveRobotsBtn");
    els.resetRobotsBtn = document.getElementById("resetRobotsBtn");
    els.taxonomyDialog = document.getElementById("taxonomyDialog");
    els.taxonomySystemList = document.getElementById("taxonomySystemList");
    els.taxonomyModuleList = document.getElementById("taxonomyModuleList");
    els.taxonomyFaultList = document.getElementById("taxonomyFaultList");
    els.taxonomySystemLabel = document.getElementById("taxonomySystemLabel");
    els.taxonomyModuleLabel = document.getElementById("taxonomyModuleLabel");
    els.addTaxonomyFaultBtn = document.getElementById("addTaxonomyFaultBtn");
    els.resetTaxonomyBtn = document.getElementById("resetTaxonomyBtn");
    els.saveTaxonomyBtn = document.getElementById("saveTaxonomyBtn");
    els.reportBtn = document.getElementById("reportBtn");
    els.exportMenu = document.getElementById("exportMenu");
    els.reportDialog = document.getElementById("reportDialog");
    els.reportCanvas = document.getElementById("reportCanvas");
    els.reportText = document.getElementById("reportText");
    els.copyReportTextBtn = document.getElementById("copyReportTextBtn");
    els.copyReportImageBtn = document.getElementById("copyReportImageBtn");
    els.downloadReportImageBtn = document.getElementById("downloadReportImageBtn");
    els.exportCsvBtn = document.getElementById("exportCsvBtn");
    els.exportJsonBtn = document.getElementById("exportJsonBtn");
    els.importJsonBtn = document.getElementById("importJsonBtn");
    els.jsonFileInput = document.getElementById("jsonFileInput");
    els.issueDialog = document.getElementById("issueDialog");
    els.issueForm = document.getElementById("issueForm");
    els.issueRobotId = document.getElementById("issueRobotId");
    els.issueOccurredAt = document.getElementById("issueOccurredAt");
    els.issueCategory = document.getElementById("issueCategory");
    els.issueModule = document.getElementById("issueModule");
    els.issueFaultSeverity = document.getElementById("issueFaultSeverity");
    els.issueSeverity = document.getElementById("issueSeverity");
    els.issueSymptom = document.getElementById("issueSymptom");
    els.issueOwner = document.getElementById("issueOwner");
    els.issueDueDate = document.getElementById("issueDueDate");
    els.issueNextAction = document.getElementById("issueNextAction");
    els.issueImpactsCollection = document.getElementById("issueImpactsCollection");
    els.issueIsP0 = document.getElementById("issueIsP0");
    els.issueDetailDialog = document.getElementById("issueDetailDialog");
    els.issueDetailForm = document.getElementById("issueDetailForm");
    els.issueDetailTitle = document.getElementById("issueDetailTitle");
    els.issueDetailMeta = document.getElementById("issueDetailMeta");
    els.issueDetailSummary = document.getElementById("issueDetailSummary");
    els.issueDetailStatus = document.getElementById("issueDetailStatus");
    els.issueDetailSeverity = document.getElementById("issueDetailSeverity");
    els.issueDetailCategory = document.getElementById("issueDetailCategory");
    els.issueDetailModule = document.getElementById("issueDetailModule");
    els.issueDetailFaultSeverity = document.getElementById("issueDetailFaultSeverity");
    els.issueDetailOwner = document.getElementById("issueDetailOwner");
    els.issueDetailDueDate = document.getElementById("issueDetailDueDate");
    els.issueDetailNextAction = document.getElementById("issueDetailNextAction");
    els.saveIssueDetailBtn = document.getElementById("saveIssueDetailBtn");
    els.issueTimeline = document.getElementById("issueTimeline");
    els.issueUpdateNote = document.getElementById("issueUpdateNote");
    els.addIssueUpdateBtn = document.getElementById("addIssueUpdateBtn");
    els.recoverIssueBtn = document.getElementById("recoverIssueBtn");
    els.closeIssueBtn = document.getElementById("closeIssueBtn");
    els.toast = document.getElementById("toast");
  }

  function bindEvents() {
    els.renameProjectBtn.addEventListener("click", openProjectDialog);
    els.projectApiBtn.addEventListener("click", openProjectApi);
    els.projectForm.addEventListener("submit", saveProject);
    document.querySelectorAll("[data-close-project-dialog]").forEach((button) => {
      button.addEventListener("click", () => els.projectDialog.close());
    });
    els.toggleProjectApiTokenBtn.addEventListener("click", toggleProjectApiToken);
    els.clearProjectApiBtn.addEventListener("click", clearProjectApiConfig);
    els.refreshProjectApiBtn.addEventListener("click", refreshProjectApi);
    els.dateInput.addEventListener("click", openDatePopover);
    els.datePickerBtn.addEventListener("click", openDatePopover);
    els.previousCalendarMonthBtn.addEventListener("click", () => shiftCalendarMonth(-1));
    els.nextCalendarMonthBtn.addEventListener("click", () => shiftCalendarMonth(1));
    els.datePickerDays.addEventListener("click", handleCalendarDayClick);
    els.clearDateRangeBtn.addEventListener("click", clearTrendDateRange);
    document.querySelectorAll("[data-trend-range]").forEach((button) => {
      button.addEventListener("click", handleDateRangeShortcut);
    });
    els.applyTrendCustomBtn.addEventListener("click", () => {
      if (!getTrendPeriod(state.activeDate, "custom")) {
        showToast("请选择完整且有效的开始、结束日期");
        return;
      }
      renderWeeklyDashboard();
      closeDatePopover();
    });
    window.addEventListener("resize", positionDatePopover);

    els.taxonomyBtn.addEventListener("click", openTaxonomy);
    els.trendRangeSelect.addEventListener("change", handleTrendRangeChange);
    els.trendCustomStart.addEventListener("change", handleTrendCustomDateChange);
    els.trendCustomEnd.addEventListener("change", handleTrendCustomDateChange);
    els.weeklyReportBtn.addEventListener("click", openWeeklyReport);
    els.copyWeeklyReportTextBtn.addEventListener("click", copyWeeklyReportText);
    els.copyWeeklyReportImageBtn.addEventListener("click", copyWeeklyReportImage);
    els.downloadWeeklyReportImageBtn.addEventListener("click", downloadWeeklyReportImage);
    els.viewAllRobotDetailsBtn.addEventListener("click", () => {
      els.problemRobotSection.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    els.scrollRobotCardsBtn.addEventListener("click", () => {
      els.weeklyRobotDetails.scrollBy({ left: Math.max(300, els.weeklyRobotDetails.clientWidth * 0.8), behavior: "smooth" });
    });
    els.weeklyRobotDetails.addEventListener("click", openRobotHistoryFromCard);
    els.weeklyIssueChart.addEventListener("pointermove", showIssueParetoTooltip);
    els.weeklyIssueChart.addEventListener("pointerleave", hideIssueParetoTooltip);
    els.weeklyIssueChart.addEventListener("focusin", showIssueParetoTooltip);
    els.weeklyIssueChart.addEventListener("focusout", hideIssueParetoTooltip);
    els.weeklySystemChart.addEventListener("pointermove", showSystemParetoTooltip);
    els.weeklySystemChart.addEventListener("pointerleave", hideSystemParetoTooltip);
    els.weeklySystemChart.addEventListener("focusin", showSystemParetoTooltip);
    els.weeklySystemChart.addEventListener("focusout", hideSystemParetoTooltip);

    els.robotTableBody.addEventListener("input", handleRecordEdit);
    els.robotTableBody.addEventListener("change", handleRecordEdit);
    els.robotTableBody.addEventListener("change", handleIssueQuickEdit);
    els.robotTableBody.addEventListener("click", handleIssueActionClick);
    els.issueQueue.addEventListener("click", handleIssueActionClick);
    els.historyList.addEventListener("click", handleIssueActionClick);
    els.historyRobotSelect.addEventListener("change", renderHistory);
    els.newIssueBtn.addEventListener("click", () => openIssueDialog());
    els.issueForm.addEventListener("submit", createIssueFromForm);
    els.issueSymptom.addEventListener("input", updateIssueClassificationPreview);
    els.issueSeverity.addEventListener("change", () => { els.issueSeverity.dataset.manual = "true"; });
    document.querySelectorAll("[data-close-issue-dialog]").forEach((button) => {
      button.addEventListener("click", () => els.issueDialog.close());
    });
    document.querySelectorAll("[data-close-issue-detail]").forEach((button) => {
      button.addEventListener("click", () => els.issueDetailDialog.close());
    });
    els.saveIssueDetailBtn.addEventListener("click", saveIssueDetail);
    els.issueDetailCategory.addEventListener("change", updateIssueDetailModuleOptions);
    els.issueDetailForm.addEventListener("submit", (event) => {
      event.preventDefault();
      saveIssueDetail();
    });
    els.addIssueUpdateBtn.addEventListener("click", addIssueUpdate);
    els.recoverIssueBtn.addEventListener("click", recoverIssue);
    els.closeIssueBtn.addEventListener("click", closeIssue);

    els.settingsBtn.addEventListener("click", openSettings);
    els.addRobotBtn.addEventListener("click", addRobotSetting);
    els.robotSettingsList.addEventListener("click", handleRobotSettingsClick);
    els.saveRobotsBtn.addEventListener("click", saveRobotSettings);
    els.resetRobotsBtn.addEventListener("click", resetRobots);
    els.taxonomySystemList.addEventListener("click", handleTaxonomySystemClick);
    els.taxonomyModuleList.addEventListener("click", handleTaxonomyModuleClick);
    els.taxonomySystemLabel.addEventListener("input", updateTaxonomySystemLabel);
    els.taxonomyModuleLabel.addEventListener("input", updateTaxonomyModuleLabel);
    els.taxonomyFaultList.addEventListener("input", updateTaxonomyFault);
    els.taxonomyFaultList.addEventListener("click", removeTaxonomyFault);
    els.addTaxonomyFaultBtn.addEventListener("click", addTaxonomyFault);
    els.resetTaxonomyBtn.addEventListener("click", resetTaxonomy);
    els.saveTaxonomyBtn.addEventListener("click", saveTaxonomy);
    els.reportBtn.addEventListener("click", () => {
      closeExportMenu();
      openReport();
    });
    els.copyReportTextBtn.addEventListener("click", copyReportText);
    els.copyReportImageBtn.addEventListener("click", copyReportImage);
    els.downloadReportImageBtn.addEventListener("click", downloadReportImage);
    els.exportCsvBtn.addEventListener("click", () => {
      closeExportMenu();
      exportCurrentCsv();
    });
    els.exportJsonBtn.addEventListener("click", () => {
      closeExportMenu();
      exportJson();
    });
    els.importJsonBtn.addEventListener("click", () => {
      closeExportMenu();
      els.jsonFileInput.click();
    });
    els.jsonFileInput.addEventListener("change", importJson);
    document.addEventListener("click", (event) => {
      if (els.exportMenu.open && !els.exportMenu.contains(event.target)) closeExportMenu();
    });
    window.addEventListener("blur", closeExportMenu);
  }

  async function loadPortfolio() {
    const localPortfolio = loadLocalPortfolio();
    if (window.parent === window) {
      state.storagePath = "浏览器本地存储（开发模式）";
      return localPortfolio;
    }
    try {
      const loaded = await requestHost("load");
      state.storagePath = String(loaded?.path || "");
      if (loaded?.store) {
        localStorage.removeItem(STORAGE_KEY);
        return normalizePortfolio(loaded.store);
      }
      const saved = await requestHost("save", { store: localPortfolio });
      state.storagePath = String(saved?.path || state.storagePath);
      localStorage.removeItem(STORAGE_KEY);
      return localPortfolio;
    } catch (error) {
      state.storageError = error.message;
      return localPortfolio;
    }
  }

  async function reloadStoreFromHost(sync) {
    try {
      const loaded = await requestHost("load");
      if (!loaded?.store) return;
      clearTimeout(state.saveTimer);
      state.saveTimer = 0;
      state.portfolio = normalizePortfolio(loaded.store);
      delete state.portfolio._needsMigrationSave;
      state.store = currentProject()?.data || createEmptyStore();
      state.storagePath = String(loaded.path || state.storagePath);
      if (sync?.periodStart) {
        state.activeDate = sync.periodStart;
        els.dateInput.value = state.activeDate;
      }
      renderAll();
      renderStorageState();
      showToast(`周报已同步到${sync?.projectName ? `“${sync.projectName}”` : "当前项目"}：${sync?.issueCount || 0} 次问题`);
    } catch (error) {
      state.storageError = error.message;
      renderStorageState();
    }
  }

  function loadLocalPortfolio() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return normalizePortfolio(createDefaultStore());
      }
      const parsed = JSON.parse(raw);
      return normalizePortfolio(parsed);
    } catch (error) {
      console.warn("Failed to load robot record store", error);
      return normalizePortfolio(createDefaultStore());
    }
  }

  function normalizePortfolio(source) {
    const now = new Date().toISOString();
    if (!projectData.isPortfolio(source)) {
      const data = normalizeStore(source);
      delete data._needsMigrationSave;
      return { ...projectData.wrapLegacyStore(data, now), _needsMigrationSave: true };
    }

    let needsMigrationSave = source.projects.length > 50;
    const seen = new Set();
    const projects = source.projects.slice(0, 50).map((project, index) => {
      let id = String(project?.id || "").trim();
      if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$/.test(id) || seen.has(id)) {
        id = createProjectId();
        needsMigrationSave = true;
      }
      seen.add(id);
      const data = normalizeStore(project?.data);
      if (data._needsMigrationSave) {
        delete data._needsMigrationSave;
        needsMigrationSave = true;
      }
      return {
        id,
        name: String(project?.name || `项目 ${index + 1}`).trim().slice(0, 60) || `项目 ${index + 1}`,
        createdAt: String(project?.createdAt || now),
        updatedAt: String(project?.updatedAt || now),
        data
      };
    });
    if (!projects.length) return normalizePortfolio(createDefaultStore());
    const requestedActiveId = String(source.activeProjectId || "");
    const activeProjectId = projects.some((project) => project.id === requestedActiveId)
      ? requestedActiveId
      : projects[0].id;
    if (activeProjectId !== requestedActiveId) needsMigrationSave = true;
    return {
      version: projectData.PORTFOLIO_VERSION,
      activeProjectId,
      projects,
      ...(needsMigrationSave ? { _needsMigrationSave: true } : {})
    };
  }

  function createDefaultStore() {
    return {
      version: 6,
      robots: createDefaultRobots(),
      records: {},
      issues: [],
      classificationCatalog: problemData.defaultClassificationCatalog(),
      problemOccurrences: []
    };
  }

  function createEmptyStore() {
    return {
      version: 6,
      robots: [],
      records: {},
      issues: [],
      classificationCatalog: problemData.defaultClassificationCatalog(),
      problemOccurrences: []
    };
  }

  function currentProject() {
    return projectData.activeProject(state.portfolio);
  }

  function createProjectId() {
    if (window.crypto?.randomUUID) return `project-${window.crypto.randomUUID()}`;
    return `project-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function commitCurrentProject() {
    const project = currentProject();
    if (!project || !state.store) return;
    project.data = state.store;
    project.updatedAt = new Date().toISOString();
  }

  function renderProjectName() {
    const project = currentProject();
    const name = project?.name || projectData.DEFAULT_PROJECT_NAME;
    els.projectName.textContent = name;
    els.projectApiName.textContent = name;
    document.title = name;
  }

  function openProjectDialog() {
    const project = currentProject();
    if (!project) return;
    els.projectForm.reset();
    els.projectNameInput.value = project.name;
    els.projectDialog.showModal();
    window.setTimeout(() => {
      els.projectNameInput.focus();
      els.projectNameInput.select();
    }, 0);
  }

  function saveProject(event) {
    event.preventDefault();
    const name = els.projectNameInput.value.trim();
    if (!name || name.length > 60 || /[\r\n\0]/.test(name)) {
      showToast("请输入 1 至 60 个字符的项目名称");
      return;
    }
    const current = currentProject();
    if (!current || current.name === name) {
      els.projectDialog.close();
      return;
    }
    current.name = name;
    current.updatedAt = new Date().toISOString();
    els.projectDialog.close();
    saveStore({ immediate: true });
    renderProjectName();
    renderStorageState();
    showToast(`看板名称已修改为“${name}”`);
  }

  function loadRemoteApiConfig() {
    try {
      const parsed = JSON.parse(localStorage.getItem(REMOTE_API_CONFIG_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (error) {
      console.warn("Failed to load remote API configuration", error);
      return {};
    }
  }

  function saveRemoteApiConfig(config) {
    localStorage.setItem(REMOTE_API_CONFIG_KEY, JSON.stringify(config));
  }

  function openProjectApi() {
    const config = loadRemoteApiConfig();
    els.projectApiName.textContent = currentProject()?.name || projectData.DEFAULT_PROJECT_NAME;
    els.projectApiStatus.textContent = config.generatedAt ? "上次读取成功" : "尚未读取";
    els.projectApiStatus.classList.remove("error");
    els.projectApiUrl.value = String(config.url || REMOTE_API_DEFAULT_URL);
    els.projectApiToken.value = String(config.apiKey || "");
    els.projectApiToken.type = "password";
    els.toggleProjectApiTokenBtn.title = "显示 API Key";
    els.toggleProjectApiTokenBtn.setAttribute("aria-label", "显示 API Key");
    els.toggleProjectApiTokenBtn.innerHTML = '<i data-lucide="eye"></i>';
    els.projectApiRemoteName.textContent = String(config.remoteName || "-");
    els.projectApiUpdatedAt.textContent = formatRemoteApiTime(config.generatedAt);
    els.projectApiError.hidden = true;
    els.projectApiError.textContent = "";
    els.projectApiDialog.showModal();
    refreshIcons();
  }

  function remoteApiCredentials(source = null) {
    const url = String(source ? source.url || "" : els.projectApiUrl.value).trim();
    const apiKey = String(source ? source.apiKey || "" : els.projectApiToken.value).trim();
    let parsedUrl;
    try {
      parsedUrl = new URL(url);
    } catch {
      throw new Error("请输入完整、有效的接口地址");
    }
    if (!["http:", "https:"].includes(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password) {
      throw new Error("接口地址仅支持不含账号密码的 HTTP 或 HTTPS 地址");
    }
    if (!REMOTE_API_KEY_PATTERN.test(apiKey)) {
      throw new Error("API Key 格式不正确");
    }
    return { url: parsedUrl.href, apiKey };
  }

  function setProjectApiControlsDisabled(disabled) {
    state.projectApiRefreshing = disabled;
    els.projectApiUrl.disabled = disabled;
    els.projectApiToken.disabled = disabled;
    els.toggleProjectApiTokenBtn.disabled = disabled;
    els.clearProjectApiBtn.disabled = disabled;
    els.refreshProjectApiBtn.disabled = disabled;
    els.refreshProjectApiBtn.innerHTML = disabled
      ? '<i data-lucide="loader-circle"></i>读取中'
      : '<i data-lucide="refresh-cw"></i>刷新 API';
    refreshIcons();
  }

  function clearProjectApiConfig() {
    localStorage.removeItem(REMOTE_API_CONFIG_KEY);
    els.projectApiUrl.value = REMOTE_API_DEFAULT_URL;
    els.projectApiToken.value = "";
    els.projectApiRemoteName.textContent = "-";
    els.projectApiUpdatedAt.textContent = "-";
    els.projectApiStatus.textContent = "尚未读取";
    els.projectApiStatus.classList.remove("error");
    els.projectApiError.hidden = true;
    showToast("API 配置已清除");
  }

  function toggleProjectApiToken() {
    const visible = els.projectApiToken.type === "text";
    els.projectApiToken.type = visible ? "password" : "text";
    els.toggleProjectApiTokenBtn.title = visible ? "显示 API Key" : "隐藏 API Key";
    els.toggleProjectApiTokenBtn.setAttribute("aria-label", els.toggleProjectApiTokenBtn.title);
    els.toggleProjectApiTokenBtn.innerHTML = `<i data-lucide="${visible ? "eye" : "eye-off"}"></i>`;
    refreshIcons();
  }

  async function refreshProjectApi({ automatic = false } = {}) {
    if (state.projectApiRefreshing) return;
    let credentials;
    try {
      const savedConfig = automatic ? loadRemoteApiConfig() : null;
      if (automatic && (!savedConfig.url || !savedConfig.apiKey)) {
        els.projectApiStatus.textContent = "未配置自动刷新";
        showToast("未配置项目 API，已保留本地台帐数据");
        return;
      }
      credentials = remoteApiCredentials(savedConfig);
    } catch (error) {
      els.projectApiStatus.textContent = automatic ? "自动刷新失败" : els.projectApiStatus.textContent;
      els.projectApiError.textContent = error.message;
      els.projectApiError.hidden = false;
      if (automatic) showToast(`台帐自动刷新失败：${error.message}`);
      return;
    }
    setProjectApiControlsDisabled(true);
    els.projectApiStatus.textContent = "正在读取";
    els.projectApiStatus.classList.remove("error");
    els.projectApiError.hidden = true;
    try {
      saveRemoteApiConfig({ ...loadRemoteApiConfig(), ...credentials });
      const payload = window.parent === window
        ? await fetchRemoteProjectDirect(credentials)
        : await requestHost("remote-load", credentials);
      if (!payload?.project || !payload?.data) throw new Error("远程 API 返回的数据格式不正确");
      state.store = normalizeStore(payload.data);
      delete state.store._needsMigrationSave;
      state.activeIssueId = "";
      state.taxonomyDraft = null;
      const config = {
        ...credentials,
        remoteName: String(payload.project.name || ""),
        generatedAt: String(payload.generatedAt || new Date().toISOString())
      };
      saveRemoteApiConfig(config);
      els.projectApiRemoteName.textContent = config.remoteName || "-";
      els.projectApiUpdatedAt.textContent = formatRemoteApiTime(config.generatedAt);
      renderAll();
      renderStorageState();
      await persistRemoteProject();
      if (state.storageError) throw new Error(`数据已读取，但保存失败：${state.storageError}`);
      els.projectApiStatus.textContent = "读取成功";
      showToast(`已读取“${config.remoteName || "远程项目"}”最新报告`);
    } catch (error) {
      els.projectApiStatus.textContent = "读取失败";
      els.projectApiStatus.classList.add("error");
      els.projectApiError.textContent = error.message;
      els.projectApiError.hidden = false;
      showToast("API 读取失败");
    } finally {
      setProjectApiControlsDisabled(false);
    }
  }

  async function fetchRemoteProjectDirect({ url, apiKey }) {
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" },
      cache: "no-store"
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload?.error?.message || `API 请求失败（${response.status}）`);
    return payload;
  }

  async function persistRemoteProject() {
    clearTimeout(state.saveTimer);
    state.saveTimer = 0;
    if (window.parent === window) {
      saveStore({ immediate: true });
      return;
    }
    await queueProjectSave();
  }

  function formatRemoteApiTime(value) {
    if (!value) return "-";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  }

  function normalizeStore(store) {
    const robots = normalizeRobots(store?.robots || []);
    const records = normalizeRecordStore(store?.records);
    const classificationCatalog = problemData.normalizeClassificationCatalog(store?.classificationCatalog);
    const hasIssueStore = Array.isArray(store?.issues);
    const issues = hasIssueStore
      ? normalizeIssues(store.issues, robots, classificationCatalog)
      : migrateLegacyIssues(records, robots, classificationCatalog);
    const normalized = {
      version: 6,
      robots,
      records,
      issues,
      classificationCatalog,
      problemOccurrences: problemData.rebuildProblemOccurrences({
        ...store,
        robots,
        issues,
        classificationCatalog
      })
    };
    if (!hasIssueStore || Number(store?.version || 0) < 6 || Object.hasOwn(store || {}, "weeklyImports")) {
      normalized._needsMigrationSave = true;
    }
    return normalized;
  }

  function createDefaultRobots() {
    return Array.from({ length: ROBOT_COUNT }, (_, index) => {
      const number = String(index + 1).padStart(2, "0");
      return {
        id: `robot-${number}`,
        code: `R${number}`,
        name: `机器人 ${number}`,
        model: "",
        location: "现场"
      };
    });
  }

  function normalizeRobots(robots) {
    const seenIds = new Set();
    return (Array.isArray(robots) ? robots : []).slice(0, 200).map((robot, index) => {
      let id = String(robot?.id || "").trim();
      if (!id || seenIds.has(id)) id = createRobotId();
      seenIds.add(id);
      const number = String(index + 1).padStart(2, "0");
      const fallbackCode = `R${number}`;
      return {
        id,
        code: String(robot?.code || fallbackCode).trim() || fallbackCode,
        name: String(robot?.name || `机器人 ${number}`).trim() || `机器人 ${number}`,
        model: String(robot?.model || "").trim(),
        location: String(robot?.location || "现场").trim() || "现场"
      };
    });
  }

  function createRobotId() {
    if (window.crypto?.randomUUID) return `robot-${window.crypto.randomUUID()}`;
    return `robot-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function saveStore(options = {}) {
    clearTimeout(state.saveTimer);
    state.store.version = 6;
    state.store.problemOccurrences = problemData.rebuildProblemOccurrences(state.store);
    commitCurrentProject();
    if (window.parent === window) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state.portfolio));
      els.autosaveState.textContent = `浏览器保存 ${formatTime(new Date())}`;
      return;
    }
    els.autosaveState.classList.remove("error");
    els.autosaveState.textContent = "等待保存";
    const delay = options.immediate ? 0 : 260;
    state.saveTimer = window.setTimeout(queueProjectSave, delay);
  }

  function queueProjectSave() {
    state.saveTimer = 0;
    commitCurrentProject();
    const snapshot = JSON.parse(JSON.stringify(state.portfolio));
    state.saveChain = state.saveChain.then(async () => {
      els.autosaveState.textContent = "保存中";
      try {
        const saved = await requestHost("save", { store: snapshot });
        state.storagePath = String(saved?.path || state.storagePath);
        state.storageError = "";
        localStorage.removeItem(STORAGE_KEY);
        renderStorageState(`已保存 ${formatTime(new Date())}`);
      } catch (error) {
        state.storageError = error.message;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
        renderStorageState("项目保存失败");
      }
    });
    return state.saveChain;
  }

  function renderStorageState(label = "") {
    els.autosaveState.classList.toggle("error", Boolean(state.storageError));
    els.autosaveState.textContent = label || (state.storageError ? "项目文件连接失败" : "项目数据已加载");
    els.autosaveState.title = state.storageError || state.storagePath;
    els.storagePathLabel.textContent = state.storageError
      ? `数据保存失败：${state.storageError}`
      : `数据：data/robot-record.json · 项目：${currentProject()?.name || "未选择"}`;
    els.storagePathLabel.title = state.storagePath;
  }

  function renderAll() {
    renderProjectName();
    renderSummary();
    renderIssueQueue();
    renderWeeklyDashboard();
    renderHistorySelect();
    renderTable();
    renderHistory();
  }

  function renderSummary() {
    const summary = getSummary(state.activeDate);
    const rate = summary.total ? Math.round((summary.normal / summary.total) * 100) : 0;
    els.availabilityRate.textContent = `${rate}%`;
    els.availableCount.textContent = `${summary.normal} / ${summary.total} 正常`;
    els.normalCount.textContent = String(summary.normal);
    els.normalRobotCodes.textContent = summary.normalRobotCodes.join("、") || "暂无";
    els.disabledCount.textContent = String(summary.disabled);
    renderStatusRobotLine(els.repairingSummary, "维修中", summary.repairingRobotCodes);
    renderStatusRobotLine(els.afterSalesSummary, "待售后", summary.afterSalesRobotCodes);
    renderStatusRobotLine(els.awayRepairSummary, "离场", summary.awayRepairRobotCodes);
    els.issueRobotCount.textContent = String(summary.openIssueCount);
    els.openIssueCount.textContent = `涉及 ${summary.issueRobotCount} 台机器人`;
    els.todayIssueCount.textContent = String(summary.todayIssueCount);
    els.overdueIssueCount.textContent = `${summary.overdueIssueCount} 项动作逾期`;
  }

  function renderStatusRobotLine(element, label, robotCodes) {
    element.hidden = robotCodes.length === 0;
    element.textContent = robotCodes.length ? `${label}：${robotCodes.join("、")}` : "";
  }

  function renderIssueQueue() {
    const issues = getOpenIssues().sort(compareIssuePriority);
    els.issueQueueCount.textContent = `${issues.length} 项`;

    if (!issues.length) {
      els.issueQueue.innerHTML = `
        <div class="empty-state issue-queue-empty">
          <i data-lucide="circle-check-big"></i>
          <span>当前没有未关闭问题</span>
        </div>`;
      refreshIcons();
      return;
    }

    els.issueQueue.innerHTML = issues.map((issue) => {
      const robot = robotForIssue(issue);
      const overdue = isIssueOverdue(issue);
      return `
        <article class="issue-queue-row${issue.isP0 ? " p0" : ""}${issue.impactsCollection ? " impacting" : ""}">
          <div class="issue-queue-robot">
            <strong>${h(robot.code)}</strong>
            <span>${h(issue.id)}</span>
          </div>
          <div class="issue-queue-main">
            <div class="issue-queue-badges">
              ${issue.isP0 ? `<span class="badge critical">P0</span>` : ""}
              <span class="badge ${h(issue.priority)}">${h(labelFor(severityOptions, issue.priority))}</span>
              <span class="badge">${h(categoryLabel(issue.systemCategory))}</span>
              ${issue.impactsCollection ? `<span class="badge impacting">影响采集</span>` : ""}
            </div>
            <strong>${h(issue.symptom)}</strong>
          </div>
          <div class="issue-queue-stage">
            <span class="badge issue-status-${h(issue.status)}">${h(labelFor(issueStatusOptions, issue.status))}</span>
            <small>持续 ${h(formatIssueAge(issue))}</small>
          </div>
          <div class="issue-queue-owner">
            <strong>${h(issue.owner || "未分配")}</strong>
            <span class="${overdue ? "overdue" : ""}">${h(issue.nextAction || "尚未填写下一步")}</span>
            ${issue.dueDate ? `<small class="${overdue ? "overdue" : ""}">${overdue ? "已逾期 " : "计划 "}${h(issue.dueDate)}</small>` : ""}
          </div>
          <button class="issue-open-button" type="button" data-open-issue="${h(issue.id)}" title="打开问题单">
            <i data-lucide="panel-right-open"></i><span>处理</span>
          </button>
        </article>`;
    }).join("");
    refreshIcons();
  }

  function selectedProblemRecord() {
    const range = els.trendRangeSelect.value || "week";
    const period = getTrendPeriod(state.activeDate, range);
    return period
      ? weeklyData.aggregateProblemOccurrences(state.store.problemOccurrences, period, classificationCatalog())
      : null;
  }

  function selectedProblemDashboard(selectedRange = "") {
    const range = selectedRange || els.trendRangeSelect.value || "week";
    const period = getTrendPeriod(state.activeDate, range);
    return period
      ? weeklyData.buildProblemDashboard(
        state.store.problemOccurrences,
        period,
        state.store.robots.length,
        classificationCatalog()
      )
      : null;
  }

  function problemRangeLabels(range) {
    return ({
      week: { current: "本周", previous: "上周", twoPeriodsAgo: "前两周" },
      previousWeek: { current: "上周", previous: "前一周", twoPeriodsAgo: "前两周" },
      month: { current: "本月", previous: "上月", twoPeriodsAgo: "前两月" },
      previousMonth: { current: "上月", previous: "前一月", twoPeriodsAgo: "前两月" },
      custom: { current: "所选范围", previous: "前一周期", twoPeriodsAgo: "前两周期" }
    })[range] || { current: "本周", previous: "上周", twoPeriodsAgo: "前两周" };
  }

  function problemDashboardSignature(dashboard) {
    return JSON.stringify({
      period: dashboard.period,
      robotTotal: dashboard.robotTotal,
      issueTotal: dashboard.issueTotal,
      previousTotal: dashboard.previousTotal,
      twoPeriodsAgoTotal: dashboard.twoPeriodsAgoTotal,
      issues: dashboard.issues.map(({ issue, count, level, cumulativePercent }) => ({ issue, count, level, cumulativePercent })),
      systems: dashboard.systems.map(({ issue, count, level, cumulativePercent, problems }) => ({
        issue,
        count,
        level,
        cumulativePercent,
        problems
      })),
      robots: dashboard.robots.map(({ robot, count, risk, problems }) => ({
        robot,
        count,
        risk,
        problems: problems.map(({ issue, count: problemCount, details }) => ({
          issue,
          count: problemCount,
          details
        }))
      }))
    });
  }

  function paretoCountAxisMax(issues) {
    const maxCount = Math.max(...issues.map((item) => item.count), 1);
    return Math.max(5, Math.ceil(maxCount / 5) * 5);
  }

  function paretoContribution(issues, count) {
    return issues[Math.min(count - 1, issues.length - 1)]?.cumulativePercent || 0;
  }

  function renderWeeklyDashboard() {
    const range = els.trendRangeSelect.value || "week";
    const period = getTrendPeriod(state.activeDate, range);
    const labels = problemRangeLabels(range);
    const dashboard = selectedProblemDashboard();
    const weeklyComparisonDashboard = selectedProblemDashboard("week");
    const summary = getSummary(state.activeDate);
    els.trendCustomRange.hidden = range !== "custom";
    updateDateRangeShortcutState();
    els.problemRobotTotal.textContent = `${state.store.robots.length} 台`;
    els.problemRobotStatus.textContent = `${summary.normal} 台正常`;
    els.problemIssueTotalLabel.textContent = `${labels.current}问题总数`;
    els.weeklyCompareTitle.textContent = "本周 vs 前两周问题对比";

    if (!period || !dashboard) {
      els.weeklyPeriodLabel.textContent = "请选择完整且有效的开始、结束日期";
      els.weeklyReportBtn.disabled = true;
      els.weeklyImportState.textContent = "日期范围无效";
      els.weeklyImportState.title = "";
      els.weeklyIssueTotal.textContent = "0 次";
      els.problemIssueCompare.textContent = "请选择日期范围";
      els.problemTopIssue.textContent = "暂无";
      els.problemTopIssueMeta.textContent = "0 次 · 0%";
      els.problemAffectedRobots.textContent = "0 台";
      els.problemAffectedRatio.textContent = "占比 0%";
      els.weeklyRobotTotal.textContent = "0 台";
      els.weeklyDetailCount.textContent = "0 台";
      const invalid = `<div class="weekly-empty"><i data-lucide="calendar-range"></i><span>请选择日期范围</span></div>`;
      els.weeklyIssueChart.innerHTML = invalid;
      delete els.weeklyIssueChart.dataset.dashboardSignature;
      els.weeklyRobotChart.innerHTML = invalid;
      els.weeklySystemChart.innerHTML = invalid;
      els.weeklySystemTotal.textContent = "0 个系统";
      els.weeklyCompareChart.innerHTML = invalid;
      els.weeklyRobotDetails.innerHTML = invalid;
      refreshIcons();
      return;
    }

    els.weeklyPeriodLabel.textContent = `${labels.current} ${period.start} 至 ${period.end}`;
    els.weeklyReportBtn.disabled = dashboard.current.sourceCount === 0;
    els.weeklyImportState.textContent = dashboard.current.sourceCount
      ? `已汇总 ${dashboard.current.sourceCount} 个来源批次`
      : "暂无问题记录";
    els.weeklyImportState.title = "";
    els.weeklyIssueTotal.textContent = `${dashboard.issueTotal} 次`;
    renderProblemComparisonText(dashboard, labels.previous);
    els.problemTopIssue.textContent = dashboard.topIssue?.issue || "暂无";
    els.problemTopIssueMeta.textContent = dashboard.topIssue
      ? `${dashboard.topIssue.count} 次 · ${dashboard.topIssue.cumulativePercent}%`
      : "0 次 · 0%";
    els.problemAffectedRobots.textContent = `${dashboard.affectedRobotCount} 台`;
    els.problemAffectedRatio.textContent = `占比 ${dashboard.affectedRatio}%`;
    els.weeklyRobotTotal.textContent = `${dashboard.robots.length} 台`;
    els.weeklyDetailCount.textContent = `共 ${dashboard.robots.length} 台`;
    els.weeklyIssueChart.innerHTML = renderParetoChart(dashboard.issues, "问题", 650, "issue");
    els.weeklyIssueChart.dataset.dashboardSignature = problemDashboardSignature(dashboard);
    els.weeklySystemChart.innerHTML = renderParetoChart(
      dashboard.systems,
      "系统",
      els.weeklySystemChart.clientWidth - 32,
      "system"
    );
    els.weeklySystemChart.dataset.dashboardSignature = problemDashboardSignature(dashboard);
    bindParetoTooltipExit(els.weeklyIssueChart, hideIssueParetoTooltip);
    bindParetoTooltipExit(els.weeklySystemChart, hideSystemParetoTooltip);
    els.weeklySystemTotal.textContent = `${dashboard.systems.length} 个系统`;
    els.weeklyRobotChart.innerHTML = renderRobotRanking(dashboard.robots);
    els.weeklyCompareChart.innerHTML = renderWeeklyComparison(
      weeklyComparisonDashboard,
      problemRangeLabels("week")
    );
    els.weeklyRobotDetails.innerHTML = renderRobotCards(dashboard.robots, period);
    els.scrollRobotCardsBtn.disabled = dashboard.robots.length <= 4;
    refreshIcons();
  }

  function renderProblemComparisonText(dashboard, previousLabel) {
    els.problemIssueCompare.className = "";
    if (!dashboard.previous.sourceCount || dashboard.changePercent === null) {
      els.problemIssueCompare.textContent = `${previousLabel}暂无数据`;
      return;
    }
    if (dashboard.changePercent < 0) {
      els.problemIssueCompare.className = "trend-down";
      els.problemIssueCompare.textContent = `较${previousLabel} ↓ ${Math.abs(dashboard.changePercent)}%`;
      return;
    }
    if (dashboard.changePercent > 0) {
      els.problemIssueCompare.className = "trend-up";
      els.problemIssueCompare.textContent = `较${previousLabel} ↑ ${dashboard.changePercent}%`;
      return;
    }
    els.problemIssueCompare.textContent = `较${previousLabel}持平`;
  }

  function robotBreakdownInline(robots) {
    return (Array.isArray(robots) ? robots : [])
      .map((item) => Number(item.count) === 1 ? String(item.robot) : `${item.robot}:${item.count}次`)
      .join(",");
  }

  function renderParetoChart(issues, contributionLabel = "问题", minimumWidth = 650, tooltipType = "") {
    if (!issues.length) {
      return `<div class="weekly-empty"><i data-lucide="chart-no-axes-column"></i><span>所选范围暂无问题统计</span></div>`;
    }
    const countAxisMax = paretoCountAxisMax(issues);
    const chartWidth = Math.max(650, minimumWidth, issues.length * 46);
    const chartHeight = 420;
    const plot = { left: 52, right: chartWidth - 58, top: 28, bottom: 324 };
    const plotWidth = plot.right - plot.left;
    const plotHeight = plot.bottom - plot.top;
    const categoryWidth = plotWidth / issues.length;
    const xForIndex = (index) => plot.left + categoryWidth * (index + 0.5);
    const yForPercent = (percent) => plot.bottom - (percent / 100) * plotHeight;
    const yForCount = (count) => plot.bottom - (count / countAxisMax) * plotHeight;
    const percentTicks = [0, 20, 40, 60, 80, 100];
    const points = issues.map((item, index) => `${xForIndex(index)},${yForPercent(item.cumulativePercent)}`).join(" ");
    const top3Contribution = paretoContribution(issues, 3);
    const top5Contribution = paretoContribution(issues, 5);
    return `
      <div class="pareto-visual" style="--pareto-width:${chartWidth}px">
        <svg class="pareto-combo-chart" viewBox="0 0 ${chartWidth} ${chartHeight}" role="img"
          aria-label="${h(contributionLabel)}次数柱状图和累计占比折线图"
          data-bar-y-axis="0" data-line-y-axis="1" data-line-smooth="false" data-line-symbol="circle">
          <text class="pareto-axis-title" x="${plot.left}" y="14">次数</text>
          <text class="pareto-axis-title pareto-axis-title-right" x="${plot.right}" y="14">累计百分比</text>
          ${percentTicks.map((percent) => {
            const y = yForPercent(percent);
            const countLabel = countAxisMax * (percent / 100);
            return `
              <line class="pareto-grid-line" x1="${plot.left}" y1="${y}" x2="${plot.right}" y2="${y}"></line>
              <text class="pareto-axis-tick pareto-count-tick" x="${plot.left - 10}" y="${y + 4}">${countLabel}</text>
              <text class="pareto-axis-tick pareto-percent-tick" x="${plot.right + 10}" y="${y + 4}">${percent}%</text>`;
          }).join("")}
          <line class="pareto-axis-line" x1="${plot.left}" y1="${plot.top}" x2="${plot.left}" y2="${plot.bottom}"></line>
          <line class="pareto-axis-line" x1="${plot.right}" y1="${plot.top}" x2="${plot.right}" y2="${plot.bottom}"></line>
          ${issues.map((item, index) => {
            const x = xForIndex(index);
            const y = yForCount(item.count);
            const width = Math.min(36, categoryWidth * 0.58);
            const column = `
              <rect class="pareto-column ${h(item.level)}" x="${x - width / 2}" y="${y}" width="${width}" height="${plot.bottom - y}" rx="3"></rect>
              <text class="pareto-column-value" x="${x}" y="${Math.max(plot.top + 12, y - 6)}">${item.count}</text>`;
            const problemSummary = (item.problems || [])
              .map((problem) => `${problem.issue}${robotBreakdownInline(problem.robots) ? `(${robotBreakdownInline(problem.robots)})` : ""} ${problem.count} 次`)
              .join("，");
            const robotSummary = (item.robots || [])
              .map((robot) => `${robot.robot} ${robot.count} 次`)
              .join("，");
            const targetAttribute = tooltipType === "system"
              ? `data-system-pareto-index="${index}"`
              : tooltipType === "issue" ? `data-issue-pareto-index="${index}"` : "";
            const accessibleDetails = tooltipType === "system"
              ? problemSummary ? `，包含 ${problemSummary}` : ""
              : robotSummary ? `，涉及 ${robotSummary}` : "";
            return `
              ${tooltipType ? `<g class="pareto-column-target" ${targetAttribute} tabindex="0" role="img" aria-label="${h(`${item.issue} ${item.count} 次${accessibleDetails}`)}">${column}</g>` : column}
              <foreignObject class="pareto-category" x="${x - categoryWidth / 2 + 3}" y="${plot.bottom + 10}" width="${categoryWidth - 6}" height="76">
                <div xmlns="http://www.w3.org/1999/xhtml" title="${h(item.issue)}">${h(item.issue)}</div>
              </foreignObject>`;
          }).join("")}
          <line class="pareto-mark-line" x1="${plot.left}" y1="${yForPercent(80)}" x2="${plot.right}" y2="${yForPercent(80)}"></line>
          <text class="pareto-mark-label" x="${plot.right - 6}" y="${yForPercent(80) - 6}">80% 参考线</text>
          <polyline class="pareto-cumulative-line" points="${points}" fill="none"></polyline>
          ${issues.map((item, index) => `
            <circle class="pareto-line-symbol" cx="${xForIndex(index)}" cy="${yForPercent(item.cumulativePercent)}" r="4"></circle>
          `).join("")}
        </svg>
        ${tooltipType ? '<div class="pareto-problem-tooltip" role="tooltip" hidden></div>' : ''}
        <p class="pareto-contribution-summary">
          <span>TOP3 ${h(contributionLabel)}贡献：<strong>${top3Contribution}%</strong></span>
          <span>TOP5 ${h(contributionLabel)}贡献：<strong>${top5Contribution}%</strong></span>
        </p>
      </div>`;
  }

  function systemParetoTooltipContent(item) {
    return `
      <strong>${h(item.issue)}<span>${item.count} 次</span></strong>
      <small>包含问题</small>
      <ul>${(item.problems || []).map((problem) => {
        return `<li class="pareto-problem-detail"><span class="pareto-problem-name">${h(problem.issue)}</span>${paretoTooltipRobots(problem.robots)}</li>`;
      }).join("") || '<li><span>暂无问题明细</span></li>'}</ul>`;
  }

  function paretoTooltipRobots(robots) {
    const rows = (Array.isArray(robots) ? robots : []).map((robot) => `
      <span class="pareto-tooltip-robot"><span class="pareto-tooltip-robot-id"><i>•</i><span>${h(robot.robot)}</span></span><b>${robot.count}次</b></span>
    `).join("");
    return `<span class="pareto-tooltip-robots">${rows || '<span class="pareto-tooltip-empty">暂无机器人明细</span>'}</span>`;
  }

  function issueParetoTooltipContent(item) {
    return `
      <strong>${h(item.issue)}<span>${item.count} 次</span></strong>
      ${paretoTooltipRobots(item.robots)}`;
  }

  function positionParetoTooltip(event, chartElement, target, content) {
    const tooltip = chartElement.querySelector(".pareto-problem-tooltip");
    if (!tooltip) return;
    tooltip.innerHTML = content;
    tooltip.hidden = false;
    const visual = tooltip.closest(".pareto-visual");
    const chart = visual.querySelector(".pareto-combo-chart");
    const visualRect = visual.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const pointerEvent = event.type === "pointermove";
    const anchorX = pointerEvent ? event.clientX : targetRect.left + targetRect.width / 2;
    const anchorY = pointerEvent ? event.clientY : targetRect.top;
    const left = Math.min(
      Math.max(8, anchorX - visualRect.left + 12),
      Math.max(8, visual.clientWidth - tooltip.offsetWidth - 8)
    );
    const top = Math.min(
      Math.max(8, anchorY - visualRect.top + 12),
      Math.max(8, chart.clientHeight - tooltip.offsetHeight - 8)
    );
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  }

  function hideParetoTooltip(chartElement) {
    const tooltip = chartElement.querySelector(".pareto-problem-tooltip");
    if (tooltip) tooltip.hidden = true;
  }

  function bindParetoTooltipExit(chartElement, hideTooltip) {
    chartElement.querySelector(".pareto-problem-tooltip")
      ?.addEventListener("pointerleave", hideTooltip);
  }

  function showIssueParetoTooltip(event) {
    if (event.target.closest?.(".pareto-problem-tooltip")) return;
    const target = event.target.closest?.("[data-issue-pareto-index]");
    if (!target) return;
    const item = selectedProblemDashboard()?.issues[Number(target.dataset.issueParetoIndex)];
    if (item) positionParetoTooltip(event, els.weeklyIssueChart, target, issueParetoTooltipContent(item));
  }

  function hideIssueParetoTooltip() {
    hideParetoTooltip(els.weeklyIssueChart);
  }

  function showSystemParetoTooltip(event) {
    if (event.target.closest?.(".pareto-problem-tooltip")) return;
    const target = event.target.closest?.("[data-system-pareto-index]");
    if (!target) return;
    const item = selectedProblemDashboard()?.systems[Number(target.dataset.systemParetoIndex)];
    if (item) positionParetoTooltip(event, els.weeklySystemChart, target, systemParetoTooltipContent(item));
  }

  function hideSystemParetoTooltip() {
    hideParetoTooltip(els.weeklySystemChart);
  }

  function renderRobotRanking(robots) {
    if (!robots.length) {
      return `<div class="weekly-empty"><i data-lucide="list-ordered"></i><span>所选范围暂无机器人问题</span></div>`;
    }
    const max = Math.max(...robots.map((item) => item.count), 1);
    return robots.map((robot, index) => `
      <div class="robot-ranking-row">
        <span class="ranking-robot"><i class="rank-index rank-${index + 1}">${index + 1}</i><strong>${h(robot.robot)}</strong></span>
        <span class="ranking-bar-track"><i class="${h(robot.risk)}" style="width:${Math.max(5, (robot.count / max) * 100)}%"></i><strong>${robot.count}</strong></span>
        <span class="risk-badge ${h(robot.risk)}">${h(robotRiskLabel(robot.risk))}</span>
      </div>`).join("");
  }

  function renderWeeklyComparison(dashboard, labels) {
    const max = Math.max(dashboard.twoPeriodsAgoTotal, dashboard.previousTotal, dashboard.issueTotal, 1);
    return `
      <div class="comparison-bars" aria-label="${h(labels.twoPeriodsAgo)} ${dashboard.twoPeriodsAgoTotal} 次，${h(labels.previous)} ${dashboard.previousTotal} 次，${h(labels.current)} ${dashboard.issueTotal} 次">
        ${comparisonBar(labels.twoPeriodsAgo, dashboard.twoPeriodsAgoTotal, max, "earlier")}
        ${comparisonBar(labels.previous, dashboard.previousTotal, max, "previous")}
        ${comparisonBar(labels.current, dashboard.issueTotal, max, "current")}
      </div>
      <div class="comparison-summary-list">
        ${comparisonSummary(labels.previous, dashboard.changePercent)}
        ${comparisonSummary(labels.twoPeriodsAgo, dashboard.twoPeriodsAgoChangePercent)}
      </div>`;
  }

  function comparisonSummary(label, changePercent) {
    const trendClass = changePercent === null
      ? "neutral"
      : changePercent < 0 ? "improved" : changePercent > 0 ? "worsened" : "neutral";
    const trendText = changePercent === null
      ? `较${label}暂无可比数据`
      : changePercent < 0
        ? `较${label}下降 ↓ ${Math.abs(changePercent)}%`
        : changePercent > 0
          ? `较${label}上升 ↑ ${changePercent}%`
          : `较${label}持平`;
    return `<div class="comparison-summary ${trendClass}"><strong>${h(trendText)}</strong></div>`;
  }

  function comparisonBar(label, count, max, kind) {
    const height = count ? Math.max(8, (count / max) * 100) : 2;
    return `
      <span class="comparison-bar-item">
        <strong>${count} 次</strong>
        <i class="comparison-bar ${h(kind)}" style="height:${height}%"></i>
        <small>${h(label)}</small>
      </span>`;
  }

  function robotDetailsInDisplayOrder(robots) {
    const list = Array.isArray(robots) ? robots : [];
    return [
      ...list.filter((robot) => robot.robot === "未注明编号"),
      ...list.filter((robot) => robot.robot !== "未注明编号")
    ];
  }

  function renderRobotCards(robots, period) {
    if (!robots.length) {
      return `<div class="weekly-empty"><i data-lucide="bot"></i><span>所选范围暂无机器人明细</span></div>`;
    }
    return robotDetailsInDisplayOrder(robots).map((robot) => {
      const { robotRecord, latestIssue } = robotCardPresentation(robot, period);
      const historyAttribute = robotRecord ? ` data-view-robot-history="${h(robotRecord.id)}"` : "";
      return `
        <button class="robot-summary-card ${h(robot.risk)}" type="button"${historyAttribute} title="查看 ${h(robot.robot)} 机器人问题历史">
          <span class="robot-card-heading">
            <strong>${h(robot.robot)}</strong>
            <span class="risk-badge ${h(robot.risk)}">${h(robotRiskLabel(robot.risk))}</span>
            <i class="robot-card-icon" data-lucide="bot"></i>
          </span>
          <span class="robot-card-body">
            <span class="robot-card-count"><small>本期问题</small><strong>${robot.count} 次</strong></span>
            <span class="robot-card-problems">
              <small>问题明细</small>
              <span class="robot-problem-tags">
                ${robot.problems.map((problem) => `
                  <i class="${h(issueLevelForCount(problem.count))}" title="${h(problem.details.join("；") || problem.issue)}">${h(problem.issue)}（${problem.count}次）</i>`).join("")}
              </span>
            </span>
          </span>
          ${latestIssue ? `
            <span class="robot-card-footer">
              <small>最新事件</small>
              <time>${h(latestIssue.occurredAt.slice(5, 16).replace("T", " "))}</time>
              <strong>${h(latestIssue.symptom)}</strong>
            </span>` : `
            <span class="robot-card-footer">
              <small>重点问题</small>
              <strong>${h(robot.problems[0]?.issue || "暂无")}</strong>
            </span>`}
        </button>`;
    }).join("");
  }

  function robotCardPresentation(robot, period) {
    const robotRecord = state.store.robots.find((item) => item.code === robot.robot) || null;
    const latestOccurrence = state.store.problemOccurrences
      .filter((occurrence) => (
        occurrence.robotId === robot.robot && problemData.occurrenceOverlapsPeriod(occurrence, period)
      ))
      .sort((a, b) => (
        String(b.occurredAt || b.periodEnd).localeCompare(String(a.occurredAt || a.periodEnd))
      ))[0];
    const latestIssue = latestOccurrence ? {
      occurredAt: /^\d{4}-\d{2}-\d{2}/.test(latestOccurrence.occurredAt)
        ? latestOccurrence.occurredAt
        : latestOccurrence.periodEnd,
      symptom: latestOccurrence.rawIssue
    } : null;
    return { robotRecord, latestIssue };
  }

  function robotRiskLabel(risk) {
    return { highRisk: "高风险", attention: "关注", minor: "轻微" }[risk] || "轻微";
  }

  function issueLevelForCount(count) {
    if (count >= 4) return "serious";
    if (count >= 2) return "warning";
    return "normal";
  }

  function openRobotHistoryFromCard(event) {
    const card = event.target.closest("[data-view-robot-history]");
    if (!card) return;
    const robotId = card.dataset.viewRobotHistory;
    if (!state.store.robots.some((robot) => robot.id === robotId)) return;
    els.historyRobotSelect.value = robotId;
    renderHistory();
    els.historyList.closest(".side-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    showToast("已打开机器人问题历史");
  }

  function handleTrendRangeChange() {
    if (els.trendRangeSelect.value === "custom" && (!els.trendCustomStart.value || !els.trendCustomEnd.value)) {
      const period = recordData.resolveTrendPeriod(state.activeDate, "week");
      els.trendCustomStart.value = period.start;
      els.trendCustomEnd.value = period.end;
    }
    syncTrendCustomLimits();
    const period = getTrendPeriod(state.activeDate, els.trendRangeSelect.value);
    if (period) state.datePickerMonth = `${period.start.slice(0, 7)}-01`;
    renderWeeklyDashboard();
    if (isDatePopoverOpen()) renderDatePicker();
    positionDatePopover();
  }

  function handleTrendCustomDateChange(event) {
    const start = els.trendCustomStart.value;
    const end = els.trendCustomEnd.value;
    if (start && end && start > end) {
      if (event.target === els.trendCustomStart) els.trendCustomEnd.value = start;
      else els.trendCustomStart.value = end;
    }
    syncTrendCustomLimits();
    const period = getTrendPeriod(state.activeDate, "custom");
    if (period) state.datePickerMonth = `${period.start.slice(0, 7)}-01`;
    renderWeeklyDashboard();
    if (isDatePopoverOpen()) renderDatePicker();
    positionDatePopover();
  }

  function syncTrendCustomLimits() {
    els.trendCustomStart.max = els.trendCustomEnd.value || "";
    els.trendCustomEnd.min = els.trendCustomStart.value || "";
  }

  function setActiveDate(date) {
    state.activeDate = date || toLocalDateString(new Date());
    els.dateInput.value = state.activeDate;
    state.datePickerMonth = `${state.activeDate.slice(0, 7)}-01`;
    renderAll();
    if (isDatePopoverOpen()) renderDatePicker();
  }

  function openDatePopover() {
    const period = getTrendPeriod(state.activeDate, els.trendRangeSelect.value || "week");
    state.datePickerMonth = `${(period?.start || state.activeDate).slice(0, 7)}-01`;
    renderDatePicker();
    if (!isDatePopoverOpen()) els.datePopover.showPopover();
    window.requestAnimationFrame(positionDatePopover);
  }

  function closeDatePopover() {
    if (isDatePopoverOpen()) els.datePopover.hidePopover();
  }

  function isDatePopoverOpen() {
    return els.datePopover.matches(":popover-open");
  }

  function positionDatePopover() {
    if (!isDatePopoverOpen()) return;
    const anchor = els.dateInput.closest(".date-picker-trigger");
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 20);
    els.datePopover.style.width = `${width}px`;
    const height = els.datePopover.offsetHeight;
    const left = Math.max(10, Math.min(rect.right - width, window.innerWidth - width - 10));
    const below = rect.bottom + 8;
    const top = below + height <= window.innerHeight - 10
      ? below
      : Math.max(10, rect.top - height - 8);
    els.datePopover.style.left = `${left}px`;
    els.datePopover.style.top = `${top}px`;
  }

  function shiftCalendarMonth(offset) {
    const current = parseLocalDate(state.datePickerMonth || `${state.activeDate.slice(0, 7)}-01`);
    current.setMonth(current.getMonth() + offset, 1);
    state.datePickerMonth = toLocalDateString(current);
    renderDatePicker();
  }

  function renderDatePicker() {
    const viewDate = parseLocalDate(state.datePickerMonth || `${state.activeDate.slice(0, 7)}-01`);
    const year = viewDate.getFullYear();
    const month = viewDate.getMonth();
    const firstOfMonth = new Date(year, month, 1);
    const mondayOffset = (firstOfMonth.getDay() + 6) % 7;
    const gridStart = new Date(year, month, 1 - mondayOffset);
    const today = toLocalDateString(new Date());
    const selectedPeriod = getTrendPeriod(state.activeDate, els.trendRangeSelect.value || "week");

    els.datePickerMonthLabel.textContent = `${year}年${month + 1}月`;
    els.datePickerDays.innerHTML = Array.from({ length: 42 }, (_, index) => {
      const date = new Date(gridStart);
      date.setDate(gridStart.getDate() + index);
      const value = toLocalDateString(date);
      const inSelectedRange = selectedPeriod && value >= selectedPeriod.start && value <= selectedPeriod.end;
      const classes = [
        date.getMonth() === month ? "" : "outside-month",
        value === today ? "today" : "",
        inSelectedRange ? "in-selected-range" : "",
        value === selectedPeriod?.start ? "range-start" : "",
        value === selectedPeriod?.end ? "range-end" : "",
        value === state.activeDate ? "selected" : ""
      ].filter(Boolean).join(" ");
      return `<button class="${classes}" type="button" role="gridcell" data-calendar-date="${value}" aria-label="${value}"${inSelectedRange ? ' aria-selected="true"' : ""}${value === state.activeDate ? ' aria-current="date"' : ""}>${date.getDate()}</button>`;
    }).join("");
    updateDateRangeShortcutState();
    refreshIcons();
  }

  function handleCalendarDayClick(event) {
    const button = event.target.closest("[data-calendar-date]");
    if (!button) return;
    setActiveDate(button.dataset.calendarDate);
    closeDatePopover();
  }

  function handleDateRangeShortcut(event) {
    const range = event.currentTarget.dataset.trendRange;
    els.trendRangeSelect.value = range;
    handleTrendRangeChange();
  }

  function clearTrendDateRange() {
    els.trendRangeSelect.value = "week";
    els.trendCustomStart.value = "";
    els.trendCustomEnd.value = "";
    syncTrendCustomLimits();
    handleTrendRangeChange();
  }

  function updateDateRangeShortcutState() {
    const range = els.trendRangeSelect.value || "week";
    document.querySelectorAll("[data-trend-range]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.trendRange === range));
    });
  }

  function renderTable() {
    const rows = state.store.robots;
    els.visibleRowsLabel.textContent = `${rows.length} 台机器人`;

    if (!rows.length) {
      els.robotTableBody.innerHTML = `
        <tr>
          <td colspan="6">
            <div class="empty-state">暂无机器人</div>
          </td>
        </tr>`;
      return;
    }

    els.robotTableBody.innerHTML = rows.map((robot) => {
      const record = getRecord(state.activeDate, robot.id);
      const meta = [robot.model, robot.location].filter(Boolean).join(" / ");
      const openIssues = getOpenIssuesForRobot(robot.id).sort(compareIssuePriority);
      const primaryIssue = openIssues[0];
      const severity = primaryIssue?.priority || "low";
      return `
        <tr data-robot-id="${h(robot.id)}" data-status="${h(record.availability)}" data-severity="${h(severity)}">
          <td>
            <div class="robot-cell">
              <span class="status-dot ${h(record.availability)}" aria-hidden="true"></span>
              <div>
                <span class="robot-code">${h(robot.code)}</span>
                <span class="robot-name">${h(robot.name)}</span>
                ${meta ? `<span class="robot-meta">${h(meta)}</span>` : ""}
              </div>
            </div>
          </td>
          <td>${selectHtml(robot.id, "availability", record.availability, availabilityOptions)}</td>
          <td>${selectHtml(robot.id, "repairStatus", record.repairStatus, repairOptions)}</td>
          <td>
            <div class="robot-issue-summary">
              ${primaryIssue ? `
                <div class="issue-quick-fields">
                  <label>
                    <span>状态</span>
                    ${issueQuickSelectHtml(primaryIssue, "status", issueStatusOptions.filter(([value]) => value !== "closed"))}
                  </label>
                  <label>
                    <span>优先级</span>
                    ${issueQuickSelectHtml(primaryIssue, "priority", severityOptions)}
                  </label>
                </div>
                <strong>${h(primaryIssue.symptom)}</strong>
                <small>${openIssues.length} 个未关闭问题</small>
              ` : `<span class="no-open-issue">无未关闭问题</span>`}
            </div>
          </td>
          <td>
            <div class="robot-next-action">
              <strong>${h(primaryIssue?.owner || "—")}</strong>
              <span>${h(primaryIssue?.nextAction || "—")}</span>
            </div>
          </td>
          <td>
            <div class="robot-row-actions">
              <button type="button" data-create-issue="${h(robot.id)}" title="记录问题"><i data-lucide="plus"></i><span>记录问题</span></button>
              ${primaryIssue ? `<button type="button" data-open-issue="${h(primaryIssue.id)}" title="处理当前问题"><i data-lucide="panel-right-open"></i><span>处理</span></button>` : ""}
            </div>
          </td>
        </tr>`;
    }).join("");
    refreshIcons();
  }

  function renderHistorySelect() {
    const robots = getHistoryRobots();
    const current = els.historyRobotSelect.value || robots[0]?.id || "";
    els.historyRobotSelect.innerHTML = robots.map((robot) => (
      `<option value="${h(robot.id)}">${h(robot.code)} ${h(robot.name)}${robot.archived ? "（已归档）" : ""}</option>`
    )).join("");
    if (robots.some((robot) => robot.id === current)) {
      els.historyRobotSelect.value = current;
    }
  }

  function getHistoryRobots() {
    const robots = state.store.robots.map((robot) => ({ ...robot, archived: false }));
    const knownIds = new Set(robots.map((robot) => robot.id));
    const knownCodes = new Set(robots.map((robot) => robot.code));
    state.store.issues.forEach((issue) => {
      if (!knownIds.has(issue.robotId)) {
        knownIds.add(issue.robotId);
        robots.push({
          id: issue.robotId,
          code: issue.robotCode || "未知设备",
          name: issue.robotName || "",
          archived: true
        });
      }
    });
    state.store.problemOccurrences.forEach((occurrence) => {
      if (occurrence.robotId === "未注明编号" || knownCodes.has(occurrence.robotId)) return;
      knownCodes.add(occurrence.robotId);
      robots.push({
        id: `occurrence-${problemData.stableHash(occurrence.robotId)}`,
        code: occurrence.robotId,
        name: "日报问题记录",
        archived: true
      });
    });
    return robots;
  }

  function renderHistory() {
    const robotId = els.historyRobotSelect.value || state.store.robots[0]?.id;
    if (!robotId) {
      els.historyList.innerHTML = `<div class="empty-state">暂无机器人</div>`;
      return;
    }

    const selectedRobot = getHistoryRobots().find((robot) => robot.id === robotId);
    const issues = state.store.issues
      .filter((issue) => issue.robotId === robotId)
      .map((issue) => ({ kind: "issue", at: issue.occurredAt, issue }));
    const occurrences = state.store.problemOccurrences
      .filter((occurrence) => occurrence.robotId === selectedRobot?.code && occurrence.sourceType !== "manual_issue")
      .map((occurrence) => ({
        kind: "occurrence",
        at: occurrence.occurredAt || occurrence.periodEnd,
        occurrence
      }));
    const historyItems = [...issues, ...occurrences]
      .sort((a, b) => b.at.localeCompare(a.at))
      .slice(0, 12);

    if (!historyItems.length) {
      els.historyList.innerHTML = `<div class="empty-state">暂无问题记录</div>`;
      return;
    }

    els.historyList.innerHTML = historyItems.map((item) => item.kind === "issue" ? `
        <button class="history-item issue-history-item" type="button" data-open-issue="${h(item.issue.id)}">
          <div class="history-title">
            <span>${h(item.issue.occurredAt.slice(0, 10))}</span>
            <span class="badge issue-status-${h(item.issue.status)}">${h(labelFor(issueStatusOptions, item.issue.status))}</span>
          </div>
          <div class="history-detail">${h(item.issue.symptom)}</div>
          <small>${h(categoryLabel(item.issue.systemCategory))} · ${h(item.issue.id)}</small>
        </button>` : `
        <article class="history-item issue-history-item occurrence-history-item">
          <div class="history-title">
            <span>${h(item.at.slice(0, 10))}</span>
            <span class="badge">${h(labelFor(faultSeverityOptions, item.occurrence.severity))}</span>
          </div>
          <div class="history-detail">${h(item.occurrence.rawIssue)}</div>
          <small>${h(item.occurrence.faultName)} · ${h(categoryLabel(item.occurrence.systemCategory))} / ${h(categoryModuleLabel(item.occurrence.systemCategory, item.occurrence.moduleCategory))}</small>
        </article>`).join("");
  }

  function handleIssueActionClick(event) {
    const createButton = event.target.closest("[data-create-issue]");
    if (createButton) {
      openIssueDialog(createButton.dataset.createIssue);
      return;
    }
    const openButton = event.target.closest("[data-open-issue]");
    if (openButton) {
      openIssueDetail(openButton.dataset.openIssue);
    }
  }

  function openIssueDialog(robotId = "") {
    const selectedRobotId = state.store.robots.some((robot) => robot.id === robotId)
      ? robotId
      : state.store.robots[0]?.id || "";
    els.issueRobotId.innerHTML = state.store.robots.map((robot) => (
      `<option value="${h(robot.id)}">${h(robot.code)} ${h(robot.name)}</option>`
    )).join("");
    els.issueCategory.innerHTML = optionsHtml(categoryOptions(), "other");
    els.issueModule.innerHTML = optionsHtml(categoryModuleOptions("other"), "other");
    els.issueFaultSeverity.innerHTML = optionsHtml(faultSeverityOptions, "info");
    els.issueSeverity.innerHTML = optionsHtml(severityOptions, "medium");
    els.issueForm.reset();
    els.issueRobotId.value = selectedRobotId;
    els.issueCategory.value = "other";
    els.issueModule.value = "other";
    els.issueFaultSeverity.value = "info";
    els.issueSeverity.value = "medium";
    delete els.issueSeverity.dataset.manual;
    els.issueOccurredAt.value = defaultIssueDateTime();
    els.issueDialog.showModal();
    els.issueSymptom.focus();
  }

  function updateIssueClassificationPreview() {
    const classification = problemData.classifyFault(els.issueSymptom.value, classificationCatalog());
    els.issueCategory.value = classification.systemCategory;
    els.issueModule.innerHTML = optionsHtml(
      categoryModuleOptions(classification.systemCategory),
      classification.moduleCategory
    );
    els.issueModule.value = classification.moduleCategory;
    els.issueFaultSeverity.value = classification.severity;
    if (els.issueSeverity.dataset.manual !== "true") {
      els.issueSeverity.value = classification.priority;
    }
  }

  function createIssueFromForm(event) {
    event.preventDefault();
    const robot = state.store.robots.find((candidate) => candidate.id === els.issueRobotId.value);
    const symptom = els.issueSymptom.value.trim();
    const owner = els.issueOwner.value.trim();
    const nextAction = els.issueNextAction.value.trim();
    const impactsCollection = els.issueImpactsCollection.checked;
    if (!robot || !symptom || !els.issueOccurredAt.value) {
      showToast("请填写机器人、发生时间和现场现象");
      return;
    }
    if (impactsCollection && (!owner || !nextAction)) {
      showToast("影响采集的问题需要填写负责人和下一步");
      (!owner ? els.issueOwner : els.issueNextAction).focus();
      return;
    }

    const now = new Date().toISOString();
    const occurredAt = normalizeDateTimeValue(els.issueOccurredAt.value);
    const classification = problemData.classifyFault(symptom, classificationCatalog());
    const issue = normalizeIssue({
      id: createIssueId(occurredAt),
      robotId: robot.id,
      robotCode: robot.code,
      robotName: robot.name,
      occurredAt,
      rawIssue: symptom,
      faultName: classification.faultName,
      systemCategory: classification.systemCategory,
      moduleCategory: classification.moduleCategory,
      faultSeverity: classification.severity,
      classificationSource: "rules",
      symptom,
      impactsCollection,
      priority: els.issueSeverity.value,
      isP0: els.issueIsP0.checked,
      owner,
      status: "open",
      nextAction,
      dueDate: els.issueDueDate.value,
      createdAt: now,
      updatedAt: now,
      source: "manual",
      timeline: [{
        id: createTimelineId(),
        at: now,
        type: "created",
        note: "问题已记录"
      }]
    }, state.store.robots, classificationCatalog());

    state.store.issues.push(issue);
    saveStore({ immediate: true });
    renderAll();
    els.issueDialog.close();
    showToast(`${issue.id} 已创建`);
    openIssueDetail(issue.id);
  }

  function openIssueDetail(issueId) {
    const issue = getIssueById(issueId);
    if (!issue) {
      showToast("未找到该问题单");
      return;
    }
    state.activeIssueId = issue.id;
    els.issueUpdateNote.value = "";
    renderIssueDetail();
    els.issueDetailDialog.showModal();
  }

  function renderIssueDetail() {
    const issue = getIssueById(state.activeIssueId);
    if (!issue) {
      els.issueDetailDialog.close();
      return;
    }
    const robot = robotForIssue(issue);
    const statusOptions = issue.status === "closed"
      ? issueStatusOptions
      : issueStatusOptions.filter(([value]) => value !== "closed");

    els.issueDetailTitle.textContent = `${robot.code} · ${issue.id}`;
    els.issueDetailMeta.textContent = `${formatDateTimeValue(issue.occurredAt)} 发生 · ${robot.name}`;
    els.issueDetailSummary.innerHTML = `
      <div class="issue-detail-badges">
        ${issue.isP0 ? `<span class="badge critical">P0</span>` : ""}
        <span class="badge ${h(issue.priority)}">${h(labelFor(severityOptions, issue.priority))}</span>
        <span class="badge">${h(labelFor(faultSeverityOptions, issue.faultSeverity))}</span>
        <span class="badge">${h(categoryLabel(issue.systemCategory))} · ${h(categoryModuleLabel(issue.systemCategory, issue.moduleCategory))}</span>
        ${issue.impactsCollection ? `<span class="badge impacting">影响采集</span>` : ""}
      </div>
      <strong>${h(issue.symptom)}</strong>
      <dl>
        <div><dt>持续时间</dt><dd>${h(formatIssueAge(issue))}</dd></div>
        <div><dt>来源</dt><dd>${issue.source === "legacy" ? "旧台账迁移" : "现场记录"}</dd></div>
        <div><dt>恢复时间</dt><dd>${issue.recoveredAt ? h(formatDateTimeValue(issue.recoveredAt)) : "—"}</dd></div>
        <div><dt>关闭时间</dt><dd>${issue.closedAt ? h(formatDateTimeValue(issue.closedAt)) : "—"}</dd></div>
      </dl>`;
    els.issueDetailStatus.innerHTML = optionsHtml(statusOptions, issue.status);
    els.issueDetailSeverity.innerHTML = optionsHtml(severityOptions, issue.priority);
    els.issueDetailCategory.innerHTML = optionsHtml(categoryOptions(), issue.systemCategory);
    els.issueDetailCategory.value = issue.systemCategory;
    els.issueDetailModule.innerHTML = optionsHtml(categoryModuleOptions(issue.systemCategory), issue.moduleCategory);
    els.issueDetailModule.value = issue.moduleCategory;
    els.issueDetailFaultSeverity.innerHTML = optionsHtml(faultSeverityOptions, issue.faultSeverity);
    els.issueDetailFaultSeverity.value = issue.faultSeverity;
    els.issueDetailOwner.value = issue.owner;
    els.issueDetailDueDate.value = issue.dueDate;
    els.issueDetailNextAction.value = issue.nextAction;
    els.issueTimeline.innerHTML = issue.timeline
      .slice()
      .sort((a, b) => b.at.localeCompare(a.at))
      .map((entry) => `
        <article class="issue-timeline-item">
          <span class="timeline-marker" aria-hidden="true"></span>
          <div>
            <div class="issue-timeline-meta">
              <strong>${h(timelineTypeLabel(entry.type))}</strong>
              <time>${h(formatDateTimeValue(entry.at))}</time>
            </div>
            <p>${h(entry.note)}</p>
          </div>
        </article>`).join("");
    els.recoverIssueBtn.disabled = issue.status === "recovered" || issue.status === "closed";
    els.closeIssueBtn.disabled = issue.status === "closed";
    refreshIcons();
  }

  function updateIssueDetailModuleOptions() {
    const options = categoryModuleOptions(els.issueDetailCategory.value);
    els.issueDetailModule.innerHTML = optionsHtml(options, options[0]?.[0] || "other");
  }

  function saveIssueDetail() {
    const issue = getIssueById(state.activeIssueId);
    if (!issue) return;
    const nextStatus = els.issueDetailStatus.value;
    if (nextStatus === "closed" && issue.status !== "closed") {
      showToast("请使用“验证并关闭”并填写验证结果");
      return;
    }
    const changes = [];
    if (issue.status !== nextStatus) {
      changes.push(`阶段：${labelFor(issueStatusOptions, issue.status)} → ${labelFor(issueStatusOptions, nextStatus)}`);
      issue.status = nextStatus;
      if (nextStatus === "recovered") issue.recoveredAt = issue.recoveredAt || new Date().toISOString();
      else if (nextStatus !== "closed") issue.recoveredAt = "";
      if (nextStatus !== "closed") issue.closedAt = "";
    }
    const nextPriority = els.issueDetailSeverity.value;
    if (issue.priority !== nextPriority) {
      changes.push(`优先级：${labelFor(severityOptions, issue.priority)} → ${labelFor(severityOptions, nextPriority)}`);
      issue.priority = nextPriority;
    }
    const classificationFields = [
      ["systemCategory", els.issueDetailCategory.value, "系统分类", categoryOptions()],
      ["moduleCategory", els.issueDetailModule.value, "模块分类", categoryModuleOptions(els.issueDetailCategory.value)],
      ["faultSeverity", els.issueDetailFaultSeverity.value, "故障等级", faultSeverityOptions]
    ];
    let classificationChanged = false;
    classificationFields.forEach(([field, value, label, options]) => {
      if (issue[field] === value) return;
      changes.push(`${label}：${labelFor(options, issue[field])} → ${labelFor(options, value)}`);
      issue[field] = value;
      classificationChanged = true;
    });
    if (classificationChanged) issue.classificationSource = "manual_correction";
    const fields = [
      ["owner", els.issueDetailOwner.value.trim(), "负责人"],
      ["dueDate", els.issueDetailDueDate.value, "计划完成"],
      ["nextAction", els.issueDetailNextAction.value.trim(), "下一步"]
    ];
    fields.forEach(([field, value, label]) => {
      if (issue[field] !== value) {
        issue[field] = value;
        changes.push(`${label}已更新`);
      }
    });
    if (!changes.length) {
      showToast("当前信息没有变化");
      return;
    }
    appendIssueTimeline(issue, "status", changes.join("；"));
    commitIssueChanges("问题信息已保存");
  }

  function addIssueUpdate() {
    const issue = getIssueById(state.activeIssueId);
    const note = els.issueUpdateNote.value.trim();
    if (!issue || !note) {
      showToast("请先填写本次处理记录");
      els.issueUpdateNote.focus();
      return;
    }
    appendIssueTimeline(issue, "update", note);
    els.issueUpdateNote.value = "";
    commitIssueChanges("处理进展已追加");
  }

  function recoverIssue() {
    const issue = getIssueById(state.activeIssueId);
    if (!issue || issue.status === "closed") return;
    const now = new Date().toISOString();
    const note = els.issueUpdateNote.value.trim() || "设备已恢复，进入验证观察";
    issue.status = "recovered";
    issue.recoveredAt = now;
    issue.closedAt = "";
    appendIssueTimeline(issue, "recovered", note, now);
    els.issueUpdateNote.value = "";
    commitIssueChanges("已标记恢复，等待验证");
  }

  function closeIssue() {
    const issue = getIssueById(state.activeIssueId);
    const note = els.issueUpdateNote.value.trim();
    if (!issue || issue.status === "closed") return;
    if (!note) {
      showToast("关闭前请填写验证结果或最终解决方案");
      els.issueUpdateNote.focus();
      return;
    }
    const now = new Date().toISOString();
    issue.status = "closed";
    issue.recoveredAt = issue.recoveredAt || now;
    issue.closedAt = now;
    issue.resolution = note;
    appendIssueTimeline(issue, "closed", note, now);
    els.issueUpdateNote.value = "";
    commitIssueChanges("问题已验证并关闭");
  }

  function commitIssueChanges(message) {
    const issue = getIssueById(state.activeIssueId);
    if (issue) issue.updatedAt = new Date().toISOString();
    saveStore({ immediate: true });
    renderSummary();
    renderIssueQueue();
    renderTable();
    renderHistory();
    renderWeeklyDashboard();
    renderIssueDetail();
    showToast(message);
  }

  function selectedTaxonomySystem() {
    return state.taxonomyDraft?.systems.find((system) => system.id === state.taxonomySystemId) || null;
  }

  function selectedTaxonomyModule() {
    return selectedTaxonomySystem()?.modules.find((module) => module.id === state.taxonomyModuleId) || null;
  }

  function openTaxonomy() {
    state.taxonomyDraft = JSON.parse(JSON.stringify(classificationCatalog()));
    state.taxonomySystemId = state.taxonomyDraft.systems[0]?.id || "other";
    state.taxonomyModuleId = selectedTaxonomySystem()?.modules[0]?.id || "other";
    renderTaxonomyEditor();
    els.taxonomyDialog.showModal();
    refreshIcons();
  }

  function renderTaxonomyEditor() {
    const system = selectedTaxonomySystem();
    const module = selectedTaxonomyModule();
    els.taxonomySystemList.innerHTML = state.taxonomyDraft.systems.map((item) => `
      <button class="taxonomy-option${item.id === state.taxonomySystemId ? " active" : ""}" type="button" data-taxonomy-system="${h(item.id)}" role="option" aria-selected="${item.id === state.taxonomySystemId}">
        <strong>${h(item.label)}</strong><small>${h(item.id)}</small>
      </button>`).join("");
    els.taxonomyModuleList.innerHTML = (system?.modules || []).map((item) => `
      <button class="taxonomy-option${item.id === state.taxonomyModuleId ? " active" : ""}" type="button" data-taxonomy-module="${h(item.id)}" role="option" aria-selected="${item.id === state.taxonomyModuleId}">
        <strong>${h(item.label)}</strong><small>${h(item.id)}</small>
      </button>`).join("");
    els.taxonomySystemLabel.value = system?.label || "";
    els.taxonomyModuleLabel.value = module?.label || "";
    els.taxonomyFaultList.innerHTML = module?.faults.length
      ? module.faults.map((fault, index) => `
        <div class="taxonomy-fault-row" data-taxonomy-fault-index="${index}">
          <label><span>标准名称</span><input data-taxonomy-fault-field="name" maxlength="80" value="${h(fault.name)}"></label>
          <label><span>匹配关键词</span><input data-taxonomy-fault-field="keywords" maxlength="600" value="${h(fault.keywords.join("、"))}"></label>
          <button class="robot-delete-button" type="button" data-remove-taxonomy-fault title="删除标准故障" aria-label="删除标准故障"><i data-lucide="trash-2"></i></button>
        </div>`).join("")
      : `<div class="empty-state">暂无标准故障</div>`;
    els.addTaxonomyFaultBtn.disabled = !module;
    refreshIcons();
  }

  function handleTaxonomySystemClick(event) {
    const button = event.target.closest("[data-taxonomy-system]");
    if (!button) return;
    state.taxonomySystemId = button.dataset.taxonomySystem;
    state.taxonomyModuleId = selectedTaxonomySystem()?.modules[0]?.id || "other";
    renderTaxonomyEditor();
  }

  function handleTaxonomyModuleClick(event) {
    const button = event.target.closest("[data-taxonomy-module]");
    if (!button) return;
    state.taxonomyModuleId = button.dataset.taxonomyModule;
    renderTaxonomyEditor();
  }

  function updateTaxonomySystemLabel() {
    const system = selectedTaxonomySystem();
    if (!system) return;
    system.label = els.taxonomySystemLabel.value.slice(0, 60);
    const label = els.taxonomySystemList.querySelector(`[data-taxonomy-system="${system.id}"] strong`);
    if (label) label.textContent = system.label || system.id;
  }

  function updateTaxonomyModuleLabel() {
    const module = selectedTaxonomyModule();
    if (!module) return;
    module.label = els.taxonomyModuleLabel.value.slice(0, 60);
    const label = els.taxonomyModuleList.querySelector(`[data-taxonomy-module="${module.id}"] strong`);
    if (label) label.textContent = module.label || module.id;
  }

  function updateTaxonomyFault(event) {
    const input = event.target.closest("[data-taxonomy-fault-field]");
    const row = input?.closest("[data-taxonomy-fault-index]");
    const fault = selectedTaxonomyModule()?.faults[Number(row?.dataset.taxonomyFaultIndex)];
    if (!input || !fault) return;
    if (input.dataset.taxonomyFaultField === "name") fault.name = input.value.slice(0, 80);
    else fault.keywords = input.value.split(/[、,，;；\n]/).map((item) => item.trim()).filter(Boolean).slice(0, 30);
  }

  function addTaxonomyFault() {
    const module = selectedTaxonomyModule();
    if (!module) return;
    module.faults.push({ name: "新标准故障", keywords: [] });
    renderTaxonomyEditor();
    els.taxonomyFaultList.querySelector('[data-taxonomy-fault-index]:last-child [data-taxonomy-fault-field="name"]')?.select();
  }

  function removeTaxonomyFault(event) {
    const button = event.target.closest("[data-remove-taxonomy-fault]");
    const row = button?.closest("[data-taxonomy-fault-index]");
    if (!button || !row) return;
    selectedTaxonomyModule()?.faults.splice(Number(row.dataset.taxonomyFaultIndex), 1);
    renderTaxonomyEditor();
  }

  function resetTaxonomy() {
    if (!window.confirm("恢复默认三级分类？自定义名称、标准故障和关键词会被替换。")) return;
    state.taxonomyDraft = problemData.defaultClassificationCatalog();
    state.taxonomySystemId = state.taxonomyDraft.systems[0]?.id || "other";
    state.taxonomyModuleId = selectedTaxonomySystem()?.modules[0]?.id || "other";
    renderTaxonomyEditor();
  }

  function saveTaxonomy() {
    const normalized = problemData.normalizeClassificationCatalog(state.taxonomyDraft);
    state.store.classificationCatalog = normalized;
    saveStore({ immediate: true });
    renderAll();
    els.taxonomyDialog.close();
    showToast("故障分类已保存");
  }

  function openSettings() {
    els.robotSettingsList.innerHTML = state.store.robots
      .map((robot, index) => robotSettingRowHtml(robot, index))
      .join("");
    els.settingsDialog.showModal();
    refreshIcons();
  }

  function robotSettingRowHtml(robot, index) {
    return `
      <div class="robot-setting-row" data-robot-id="${h(robot.id)}">
        <span class="robot-setting-number">#${String(index + 1).padStart(2, "0")}</span>
        <input data-setting-field="code" value="${h(robot.code)}" aria-label="机器人编号" placeholder="编号">
        <input data-setting-field="name" value="${h(robot.name)}" aria-label="机器人名称" placeholder="名称">
        <input data-setting-field="model" value="${h(robot.model)}" aria-label="机器人型号" placeholder="型号">
        <input data-setting-field="location" value="${h(robot.location)}" aria-label="机器人位置" placeholder="位置">
        <button class="robot-delete-button" type="button" data-delete-robot title="删除机器人" aria-label="删除机器人">
          <i data-lucide="trash-2"></i>
        </button>
      </div>`;
  }

  function addRobotSetting() {
    const rows = Array.from(els.robotSettingsList.querySelectorAll(".robot-setting-row"));
    const existingCodes = new Set(rows.map((row) => getSettingValue(row, "code")));
    let number = rows.length + 1;
    let code = `R${String(number).padStart(2, "0")}`;
    while (existingCodes.has(code)) {
      number += 1;
      code = `R${String(number).padStart(2, "0")}`;
    }
    const robot = {
      id: createRobotId(),
      code,
      name: `机器人 ${String(number).padStart(2, "0")}`,
      model: "",
      location: "现场"
    };
    els.robotSettingsList.insertAdjacentHTML("beforeend", robotSettingRowHtml(robot, rows.length));
    const added = els.robotSettingsList.lastElementChild;
    refreshIcons();
    added.scrollIntoView({ block: "nearest", behavior: "smooth" });
    added.querySelector('[data-setting-field="code"]')?.focus();
  }

  function handleRobotSettingsClick(event) {
    const button = event.target.closest("[data-delete-robot]");
    if (!button) return;
    const row = button.closest(".robot-setting-row");
    const existing = state.store.robots.find((robot) => robot.id === row?.dataset.robotId);
    if (existing && !window.confirm(`删除 ${existing.code} ${existing.name}？每日状态会移出清单，已有问题单仍会保留用于追溯。`)) {
      return;
    }
    row?.remove();
    renumberRobotSettingRows();
  }

  function renumberRobotSettingRows() {
    els.robotSettingsList.querySelectorAll(".robot-setting-row").forEach((row, index) => {
      const number = row.querySelector(".robot-setting-number");
      if (number) number.textContent = `#${String(index + 1).padStart(2, "0")}`;
    });
  }

  function saveRobotSettings() {
    const rows = Array.from(els.robotSettingsList.querySelectorAll(".robot-setting-row"));
    const robots = rows.map((row, index) => {
      const number = String(index + 1).padStart(2, "0");
      return {
        id: row.dataset.robotId || createRobotId(),
        code: getSettingValue(row, "code") || `R${number}`,
        name: getSettingValue(row, "name") || `机器人 ${number}`,
        model: getSettingValue(row, "model"),
        location: getSettingValue(row, "location") || "现场"
      };
    });
    const duplicateCode = robots.find((robot, index) => (
      robots.findIndex((candidate) => candidate.code === robot.code) !== index
    ));
    if (duplicateCode) {
      showToast(`机器人编号 ${duplicateCode.code} 重复`);
      rows.find((row) => getSettingValue(row, "code") === duplicateCode.code)
        ?.querySelector('[data-setting-field="code"]')
        ?.focus();
      return;
    }
    const nextIds = new Set(robots.map((robot) => robot.id));
    const removedIds = state.store.robots
      .map((robot) => robot.id)
      .filter((id) => !nextIds.has(id));
    removeRobotHistory(removedIds);
    state.store.robots = normalizeRobots(robots);
    state.store.issues.forEach((issue) => {
      const robot = state.store.robots.find((candidate) => candidate.id === issue.robotId);
      if (robot) {
        issue.robotCode = robot.code;
        issue.robotName = robot.name;
      }
    });
    saveStore({ immediate: true });
    renderAll();
    els.settingsDialog.close();
    showToast("机器人清单已保存");
  }

  function removeRobotHistory(robotIds) {
    if (!robotIds.length) return;
    Object.keys(state.store.records).forEach((date) => {
      const records = state.store.records[date];
      robotIds.forEach((robotId) => delete records[robotId]);
      if (!Object.keys(records).length) delete state.store.records[date];
    });
  }

  function resetRobots() {
    if (!window.confirm("确定恢复默认 11 台机器人清单？历史记录会保留。")) {
      return;
    }
    state.store.robots = createDefaultRobots();
    saveStore();
    renderAll();
    els.settingsDialog.close();
    showToast("机器人清单已恢复默认");
  }

  function handleRecordEdit(event) {
    const control = event.target.closest("[data-field]");
    if (!control) {
      return;
    }

    const record = ensureRecord(state.activeDate, control.dataset.robotId);
    record[control.dataset.field] = control.value;
    record.updatedAt = new Date().toISOString();
    saveStore();
    updateRowState(control.dataset.robotId, record);
    renderSummary();
  }

  function handleIssueQuickEdit(event) {
    const control = event.target.closest("[data-issue-field]");
    if (!control) return;
    const issue = getIssueById(control.dataset.issueId);
    const field = control.dataset.issueField;
    const options = field === "status" ? issueStatusOptions : field === "priority" ? severityOptions : [];
    if (!issue || !options.some(([value]) => value === control.value) || issue[field] === control.value) return;

    const previous = issue[field];
    const now = new Date().toISOString();
    issue[field] = control.value;
    if (field === "status") {
      if (control.value === "recovered") issue.recoveredAt = issue.recoveredAt || now;
      else issue.recoveredAt = "";
      issue.closedAt = "";
    }
    const label = field === "status" ? "阶段" : "优先级";
    appendIssueTimeline(issue, "status", `${label}：${labelFor(options, previous)} → ${labelFor(options, control.value)}`, now);
    saveStore({ immediate: true });
    renderIssueQueue();
    renderTable();
    renderHistory();
    if (state.activeIssueId === issue.id && els.issueDetailDialog.open) renderIssueDetail();
    showToast(`${label}已更新`);
  }

  function updateRowState(robotId, record) {
    const row = els.robotTableBody.querySelector(`tr[data-robot-id="${cssEscape(robotId)}"]`);
    if (!row) {
      return;
    }
    row.dataset.status = record.availability;
    const dot = row.querySelector(".status-dot");
    if (dot) {
      dot.className = `status-dot ${record.availability}`;
    }
  }

  function openWeeklyReport() {
    const weeklyImport = selectedProblemRecord();
    if (!weeklyImport?.sourceCount) {
      showToast("所选范围内没有可生成的记录");
      return;
    }
    els.weeklyReportPeriod.textContent = `${weeklyImport.weekStart} 至 ${weeklyImport.weekEnd}`;
    els.weeklyReportText.value = weeklyData.buildProblemRecordText(weeklyImport);
    renderWeeklyReportImage(weeklyImport);
    els.weeklyReportDialog.showModal();
    els.copyWeeklyReportImageBtn.focus();
  }

  async function copyWeeklyReportText() {
    const weeklyImport = selectedProblemRecord();
    const text = els.weeklyReportText.value || weeklyData.buildProblemRecordText(weeklyImport);
    try {
      await navigator.clipboard.writeText(text);
      showToast("记录文本已复制");
    } catch {
      const details = els.weeklyReportText.closest("details");
      if (details) details.open = true;
      els.weeklyReportText.focus();
      els.weeklyReportText.select();
      showToast("已选中文本，可手动复制");
    }
  }

  async function copyWeeklyReportImage() {
    const blob = await weeklyReportBlob();
    if (!blob) return;
    if (navigator.clipboard && window.ClipboardItem) {
      try {
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
        showToast("问题记录图片已复制");
        return;
      } catch (error) {
        console.warn("Failed to copy weekly report image", error);
      }
    }
    downloadBlob(weeklyReportFilename(), blob);
    showToast("浏览器不支持直接复制图片，已下载 PNG");
  }

  async function downloadWeeklyReportImage() {
    const blob = await weeklyReportBlob();
    if (!blob) return;
    downloadBlob(weeklyReportFilename(), blob);
    showToast("问题记录图片已下载");
  }

  async function weeklyReportBlob() {
    const weeklyImport = selectedProblemRecord();
    if (!weeklyImport?.sourceCount) {
      showToast("所选范围内没有问题记录");
      return null;
    }
    if (!els.weeklyReportCanvas.width || !els.weeklyReportCanvas.height) {
      renderWeeklyReportImage(weeklyImport);
    }
    const blob = await canvasToPngBlob(els.weeklyReportCanvas);
    if (!blob) showToast("问题记录图片生成失败");
    return blob;
  }

  function weeklyReportFilename() {
    const weeklyImport = selectedProblemRecord();
    return `robot-problem-record-${weeklyImport?.weekStart || state.activeDate}-${weeklyImport?.weekEnd || state.activeDate}.png`;
  }

  function renderWeeklyReportImage(weeklyImport) {
    const canvas = els.weeklyReportCanvas;
    const width = 1280;
    const scale = 2;
    const margin = 48;
    const gap = 20;
    const contentWidth = width - margin * 2;
    const dashboard = weeklyData.buildProblemDashboard(
      state.store.problemOccurrences,
      { start: weeklyImport.weekStart, end: weeklyImport.weekEnd },
      state.store.robots.length,
      classificationCatalog()
    );
    if (!dashboard) return;
    const range = els.trendRangeSelect.value || "week";
    const labels = problemRangeLabels(range);
    const statusSummary = getSummary(state.activeDate);
    const issues = dashboard.issues;
    const robots = dashboard.robots;
    const detailRobots = robotDetailsInDisplayOrder(robots);
    const total = dashboard.issueTotal;
    const measure = document.createElement("canvas").getContext("2d");
    const detailColumns = 3;
    const detailGap = 14;
    const detailCardWidth = (contentWidth - detailGap * (detailColumns - 1)) / detailColumns;
    setReportFont(measure, 600, 12);
    const detailCardHeights = detailRobots.map((robot) => measureWeeklyRobotCardHeight(measure, robot, detailCardWidth));
    const detailRowHeights = [];
    for (let index = 0; index < detailCardHeights.length; index += detailColumns) {
      detailRowHeights.push(Math.max(...detailCardHeights.slice(index, index + detailColumns)));
    }
    const analysisHeight = 520;
    const paretoWidth = 760;
    const sideWidth = contentWidth - paretoWidth - gap;
    const rankingHeight = 300;
    const comparisonHeight = analysisHeight - rankingHeight - gap;
    const systemChartHeight = 520;
    const headerY = 40;
    const headerHeight = 124;
    const statusTitleY = headerY + headerHeight + 28;
    const statusCardsY = statusTitleY + 46;
    const statusCardsHeight = 118;
    const summaryY = statusCardsY + statusCardsHeight + 28;
    const summaryHeight = 104;
    const chartsY = summaryY + summaryHeight + 28;
    const systemChartY = chartsY + analysisHeight + gap;
    const detailsTitleY = systemChartY + systemChartHeight + 34;
    const detailsY = detailsTitleY + 44;
    const detailsHeight = detailRowHeights.length
      ? detailRowHeights.reduce((sum, rowHeight) => sum + rowHeight, 0) + Math.max(0, detailRowHeights.length - 1) * detailGap
      : 66;
    const height = Math.ceil(detailsY + detailsHeight + 70);
    const ctx = canvas.getContext("2d");

    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.style.aspectRatio = `${width} / ${height}`;
    canvas.dataset.dashboardSignature = problemDashboardSignature(dashboard);
    canvas.dataset.paretoBarAxis = "0";
    canvas.dataset.paretoLineAxis = "1";
    canvas.dataset.paretoLineSmooth = "false";
    canvas.dataset.paretoLineSymbol = "circle";
    canvas.dataset.paretoMarkLine = "80";
    canvas.dataset.systemPareto = "true";
    canvas.dataset.statusCardCount = "5";
    canvas.dataset.statusDate = state.activeDate;
    canvas.dataset.robotDetailLayout = "cards";
    canvas.dataset.robotCardCount = String(robots.length);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.fillStyle = reportTheme.bg;
    ctx.fillRect(0, 0, width, height);

    fillRoundedRect(ctx, margin, headerY, contentWidth, headerHeight, 8, reportTheme.ink);
    setReportFont(ctx, 700, 20);
    ctx.fillStyle = "rgba(255,255,255,0.72)";
    ctx.fillText("机器人台账 · 问题记录", margin + 30, headerY + 38);
    setReportFont(ctx, 800, 40);
    ctx.fillStyle = "#ffffff";
    ctx.fillText("机器人问题记录", margin + 30, headerY + 86);
    setReportFont(ctx, 700, 22);
    ctx.fillStyle = "rgba(255,255,255,0.86)";
    drawTextRight(ctx, `${weeklyImport.weekStart} 至 ${weeklyImport.weekEnd}`, margin + contentWidth - 30, headerY + 46);
    setReportFont(ctx, 800, 30);
    ctx.fillStyle = reportTheme.greenSoft;
    drawTextRight(ctx, `${total} 次问题`, margin + contentWidth - 30, headerY + 91);

    drawSectionTitle(ctx, "机器人状态", `状态日期 ${state.activeDate}`, margin, statusTitleY, contentWidth);
    drawWeeklyStatusCards(ctx, {
      x: margin,
      y: statusCardsY,
      width: contentWidth,
      height: statusCardsHeight,
      summary: statusSummary
    });

    fillRoundedRect(ctx, margin, summaryY, contentWidth, summaryHeight, 8, reportTheme.surface);
    strokeRoundedRect(ctx, margin, summaryY, contentWidth, summaryHeight, 8, reportTheme.line);
    const summaryItems = [
      { label: "机器人总数", value: `${dashboard.robotTotal} 台`, helper: `${statusSummary.normal} 台正常` },
      {
        label: `${labels.current}问题总数`,
        value: `${total} 次`,
        helper: dashboard.previous.sourceCount && dashboard.changePercent !== null
          ? `较${labels.previous} ${dashboard.changePercent > 0 ? "↑" : dashboard.changePercent < 0 ? "↓" : ""} ${Math.abs(dashboard.changePercent)}%`
          : `${labels.previous}暂无数据`
      },
      {
        label: "TOP 问题",
        value: dashboard.topIssue?.issue || "暂无",
        helper: dashboard.topIssue ? `${dashboard.topIssue.count} 次 · ${dashboard.topIssue.cumulativePercent}%` : "0 次 · 0%"
      },
      { label: "受影响机器人", value: `${dashboard.affectedRobotCount} 台`, helper: `占比 ${dashboard.affectedRatio}%` }
    ];
    summaryItems.forEach((item, index) => {
      const x = margin + 24 + index * (contentWidth / 4);
      setReportFont(ctx, 700, 16);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText(item.label, x, summaryY + 25);
      setReportFont(ctx, 800, 26);
      ctx.fillStyle = index === 1 ? reportTheme.red : reportTheme.ink;
      ctx.fillText(truncateCanvasText(ctx, item.value, contentWidth / 4 - 44), x, summaryY + 58);
      setReportFont(ctx, 600, 15);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText(truncateCanvasText(ctx, item.helper, contentWidth / 4 - 44), x, summaryY + 84);
    });

    drawWeeklyParetoCard(ctx, {
      x: margin,
      y: chartsY,
      width: paretoWidth,
      height: analysisHeight,
      issues,
      title: "问题统计（Pareto）",
      contributionLabel: "问题"
    });
    drawWeeklyRobotRankingCard(ctx, {
      x: margin + paretoWidth + gap,
      y: chartsY,
      width: sideWidth,
      height: rankingHeight,
      robots
    });
    drawWeeklyComparisonCard(ctx, {
      x: margin + paretoWidth + gap,
      y: chartsY + rankingHeight + gap,
      width: sideWidth,
      height: comparisonHeight,
      dashboard,
      labels
    });
    drawWeeklyParetoCard(ctx, {
      x: margin,
      y: systemChartY,
      width: contentWidth,
      height: systemChartHeight,
      issues: dashboard.systems,
      title: "故障来源（系统 Pareto）",
      contributionLabel: "系统"
    });

    drawSectionTitle(ctx, "机器人明细", `高风险 · 关注 · 轻微  ·  ${robots.length} 台`, margin, detailsTitleY, contentWidth);
    let y = detailsY;
    if (!detailRobots.length) {
      fillRoundedRect(ctx, margin, y, contentWidth, 66, 8, reportTheme.surface);
      strokeRoundedRect(ctx, margin, y, contentWidth, 66, 8, reportTheme.line);
      setReportFont(ctx, 700, 19);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText("暂无机器人明细", margin + 22, y + 40);
      y += 66;
    } else {
      let rowY = detailsY;
      detailRobots.forEach((robot, index) => {
        const rowIndex = Math.floor(index / detailColumns);
        const columnIndex = index % detailColumns;
        if (columnIndex === 0 && rowIndex > 0) {
          rowY += detailRowHeights[rowIndex - 1] + detailGap;
        }
        drawWeeklyRobotDetailCard(ctx, {
          x: margin + columnIndex * (detailCardWidth + detailGap),
          y: rowY,
          width: detailCardWidth,
          height: detailRowHeights[rowIndex],
          robot,
          period: dashboard.period
        });
      });
      y = detailsY + detailsHeight;
    }

  }

  function statusMetricCards(summary) {
    const rate = summary.total ? Math.round((summary.normal / summary.total) * 100) : 0;
    return [
      { label: "正常率", value: `${rate}%`, helper: `${summary.normal} / ${summary.total} 正常`, color: reportTheme.green },
      { label: "正常", value: String(summary.normal), helper: "可正常投入使用", color: reportTheme.green },
      { label: "停用", value: String(summary.disabled), helper: repairSummaryText(summary), color: reportTheme.red },
      { label: "未关闭问题", value: String(summary.openIssueCount), helper: `涉及 ${summary.issueRobotCount} 台机器人`, color: reportTheme.blue },
      { label: "当日新增", value: String(summary.todayIssueCount), helper: `${summary.overdueIssueCount} 项动作逾期`, color: reportTheme.amber }
    ];
  }

  function drawWeeklyStatusCards(ctx, layout) {
    const cards = statusMetricCards(layout.summary);
    const gap = 12;
    const cardWidth = (layout.width - gap * (cards.length - 1)) / cards.length;
    cards.forEach((card, index) => {
      const x = layout.x + index * (cardWidth + gap);
      fillRoundedRect(ctx, x, layout.y, cardWidth, layout.height, 8, reportTheme.surface);
      strokeRoundedRect(ctx, x, layout.y, cardWidth, layout.height, 8, reportTheme.line);
      ctx.fillStyle = card.color;
      ctx.fillRect(x, layout.y, cardWidth, 4);

      setReportFont(ctx, 800, 15);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText(card.label, x + 16, layout.y + 27);
      setReportFont(ctx, 800, 32);
      ctx.fillStyle = reportTheme.ink;
      ctx.fillText(card.value, x + 16, layout.y + 66);
      setReportFont(ctx, 600, 12);
      ctx.fillStyle = reportTheme.muted;
      wrapCanvasText(ctx, card.helper, cardWidth - 32).slice(0, 2).forEach((line, lineIndex) => {
        ctx.fillText(line, x + 16, layout.y + 91 + lineIndex * 16);
      });
    });
  }

  function drawWeeklyParetoCard(ctx, chart) {
    fillRoundedRect(ctx, chart.x, chart.y, chart.width, chart.height, 8, reportTheme.surface);
    strokeRoundedRect(ctx, chart.x, chart.y, chart.width, chart.height, 8, reportTheme.line);
    setReportFont(ctx, 800, 21);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(chart.title || "问题统计（Pareto）", chart.x + 22, chart.y + 34);
    setReportFont(ctx, 600, 14);
    ctx.fillStyle = reportTheme.muted;
    drawTextRight(ctx, "柱：本期次数  ·  线：累计占比", chart.x + chart.width - 22, chart.y + 33);

    const plot = {
      left: chart.x + 50,
      right: chart.x + chart.width - 58,
      top: chart.y + 78,
      bottom: chart.y + 340
    };
    const plotWidth = plot.right - plot.left;
    const plotHeight = plot.bottom - plot.top;
    const categoryWidth = plotWidth / chart.issues.length;
    const countAxisMax = paretoCountAxisMax(chart.issues);
    const yForPercent = (percent) => plot.bottom - (percent / 100) * plotHeight;
    const yForCount = (count) => plot.bottom - (count / countAxisMax) * plotHeight;
    const xForIndex = (index) => plot.left + categoryWidth * (index + 0.5);

    setReportFont(ctx, 700, 13);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText("次数", plot.left, chart.y + 64);
    drawTextRight(ctx, "累计百分比", plot.right, chart.y + 64);

    [0, 20, 40, 60, 80, 100].forEach((percent) => {
      const y = yForPercent(percent);
      ctx.strokeStyle = "#e5eaf0";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      setReportFont(ctx, 500, 12);
      ctx.fillStyle = reportTheme.muted;
      drawTextRight(ctx, String(countAxisMax * percent / 100), plot.left - 9, y + 4);
      ctx.fillText(`${percent}%`, plot.right + 9, y + 4);
    });

    chart.issues.forEach((item, index) => {
      const x = xForIndex(index);
      const y = yForCount(item.count);
      const barWidth = Math.min(28, categoryWidth * 0.58);
      fillRoundedRect(ctx, x - barWidth / 2, y, barWidth, plot.bottom - y, 3, weeklyIssueColor(item.level));
      setReportFont(ctx, 800, 12);
      ctx.fillStyle = reportTheme.ink;
      const countText = String(item.count);
      ctx.fillText(countText, x - ctx.measureText(countText).width / 2, Math.max(plot.top + 13, y - 6));

      setReportFont(ctx, 600, 11);
      ctx.fillStyle = reportTheme.ink;
      wrapCanvasText(ctx, item.issue, categoryWidth - 5).slice(0, 4).forEach((line, lineIndex) => {
        ctx.fillText(line, x - ctx.measureText(line).width / 2, plot.bottom + 17 + lineIndex * 13);
      });
    });

    const markY = yForPercent(80);
    ctx.save();
    ctx.strokeStyle = reportTheme.amber;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([7, 5]);
    ctx.beginPath();
    ctx.moveTo(plot.left, markY);
    ctx.lineTo(plot.right, markY);
    ctx.stroke();
    ctx.restore();
    setReportFont(ctx, 700, 12);
    ctx.fillStyle = reportTheme.amber;
    drawTextRight(ctx, "80% 参考线", plot.right - 4, markY - 6);

    ctx.strokeStyle = reportTheme.blue;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    chart.issues.forEach((item, index) => {
      const x = xForIndex(index);
      const y = yForPercent(item.cumulativePercent);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    chart.issues.forEach((item, index) => {
      const x = xForIndex(index);
      const y = yForPercent(item.cumulativePercent);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = reportTheme.surface;
      ctx.fill();
      ctx.strokeStyle = reportTheme.blue;
      ctx.lineWidth = 2;
      ctx.stroke();
    });

    ctx.strokeStyle = reportTheme.line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(chart.x + 22, chart.y + 468);
    ctx.lineTo(chart.x + chart.width - 22, chart.y + 468);
    ctx.stroke();
    setReportFont(ctx, 600, 14);
    ctx.fillStyle = reportTheme.muted;
    const contributionLabel = chart.contributionLabel || "问题";
    ctx.fillText(`TOP3 ${contributionLabel}贡献：`, chart.x + 22, chart.y + 495);
    setReportFont(ctx, 800, 14);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(`${paretoContribution(chart.issues, 3)}%`, chart.x + 142, chart.y + 495);
    setReportFont(ctx, 600, 14);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText(`TOP5 ${contributionLabel}贡献：`, chart.x + 220, chart.y + 495);
    setReportFont(ctx, 800, 14);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(`${paretoContribution(chart.issues, 5)}%`, chart.x + 340, chart.y + 495);
  }

  function drawWeeklyRobotRankingCard(ctx, chart) {
    fillRoundedRect(ctx, chart.x, chart.y, chart.width, chart.height, 8, reportTheme.surface);
    strokeRoundedRect(ctx, chart.x, chart.y, chart.width, chart.height, 8, reportTheme.line);
    setReportFont(ctx, 800, 20);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText("机器人问题频次排行", chart.x + 20, chart.y + 32);
    setReportFont(ctx, 700, 14);
    ctx.fillStyle = reportTheme.muted;
    drawTextRight(ctx, `${chart.robots.length} 台`, chart.x + chart.width - 20, chart.y + 31);
    const max = Math.max(...chart.robots.map((item) => item.count), 1);
    chart.robots.slice(0, 6).forEach((robot, index) => {
      const y = chart.y + 65 + index * 37;
      const color = weeklyRobotRiskColor(robot.risk);
      ctx.beginPath();
      ctx.arc(chart.x + 26, y - 4, 11, 0, Math.PI * 2);
      ctx.fillStyle = reportTheme.graySoft;
      ctx.fill();
      setReportFont(ctx, 800, 12);
      ctx.fillStyle = reportTheme.muted;
      const rank = String(index + 1);
      ctx.fillText(rank, chart.x + 26 - ctx.measureText(rank).width / 2, y);
      setReportFont(ctx, 800, 15);
      ctx.fillStyle = reportTheme.ink;
      ctx.fillText(truncateCanvasText(ctx, robot.robot, 84), chart.x + 46, y + 1);
      const trackX = chart.x + 140;
      const trackWidth = chart.width - 242;
      fillRoundedRect(ctx, trackX, y - 11, trackWidth, 10, 3, reportTheme.graySoft);
      fillRoundedRect(ctx, trackX, y - 11, Math.max(5, trackWidth * robot.count / max), 10, 3, color);
      setReportFont(ctx, 800, 14);
      ctx.fillStyle = reportTheme.ink;
      drawTextRight(ctx, String(robot.count), chart.x + chart.width - 72, y);
      setReportFont(ctx, 700, 13);
      ctx.fillStyle = color;
      drawTextRight(ctx, robotRiskLabel(robot.risk), chart.x + chart.width - 18, y);
    });
  }

  function drawWeeklyComparisonCard(ctx, chart) {
    fillRoundedRect(ctx, chart.x, chart.y, chart.width, chart.height, 8, reportTheme.surface);
    strokeRoundedRect(ctx, chart.x, chart.y, chart.width, chart.height, 8, reportTheme.line);
    setReportFont(ctx, 800, 19);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(`${chart.labels.current} vs ${chart.labels.previous}问题对比`, chart.x + 20, chart.y + 31);
    const max = Math.max(chart.dashboard.previousTotal, chart.dashboard.issueTotal, 1);
    const baseY = chart.y + chart.height - 30;
    const maxHeight = 92;
    const bars = [
      { label: chart.labels.previous, count: chart.dashboard.previousTotal, x: chart.x + 82, color: "#cbd5e1" },
      { label: chart.labels.current, count: chart.dashboard.issueTotal, x: chart.x + 172, color: reportTheme.blue }
    ];
    bars.forEach((bar) => {
      const height = bar.count ? Math.max(4, maxHeight * bar.count / max) : 2;
      fillRoundedRect(ctx, bar.x, baseY - height, 42, height, 3, bar.color);
      setReportFont(ctx, 800, 14);
      ctx.fillStyle = reportTheme.ink;
      const value = `${bar.count} 次`;
      ctx.fillText(value, bar.x + 21 - ctx.measureText(value).width / 2, baseY - height - 8);
      setReportFont(ctx, 600, 13);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText(bar.label, bar.x + 21 - ctx.measureText(bar.label).width / 2, baseY + 19);
    });
    const changeText = chart.dashboard.changePercent === null
      ? "暂无环比"
      : chart.dashboard.changePercent < 0
        ? `环比下降 ${Math.abs(chart.dashboard.changePercent)}%`
        : chart.dashboard.changePercent > 0
          ? `环比上升 ${chart.dashboard.changePercent}%`
          : "环比持平";
    setReportFont(ctx, 800, 17);
    ctx.fillStyle = chart.dashboard.changePercent > 0 ? reportTheme.red : reportTheme.ink;
    drawTextRight(ctx, changeText, chart.x + chart.width - 20, chart.y + 78);
    setReportFont(ctx, 600, 13);
    ctx.fillStyle = reportTheme.muted;
    drawTextRight(ctx, `${chart.labels.previous}：${chart.dashboard.previousTotal} 次`, chart.x + chart.width - 20, chart.y + 107);
    drawTextRight(ctx, `${chart.labels.current}：${chart.dashboard.issueTotal} 次`, chart.x + chart.width - 20, chart.y + 130);
  }

  function weeklyIssueColor(level) {
    return { serious: "#ef4444", warning: "#f59e0b", normal: reportTheme.blue }[level] || reportTheme.blue;
  }

  function weeklyRobotRiskColor(risk) {
    return { highRisk: "#ef4444", attention: "#f59e0b", minor: reportTheme.blue }[risk] || reportTheme.blue;
  }

  function weeklyRobotTagLayout(ctx, problems, maxWidth) {
    const placements = [];
    let x = 0;
    let y = 0;
    problems.forEach((problem) => {
      const label = `${problem.issue}（${problem.count}次）`;
      const width = Math.min(maxWidth, Math.ceil(ctx.measureText(label).width) + 16);
      if (x > 0 && x + width > maxWidth) {
        x = 0;
        y += 30;
      }
      placements.push({ problem, label, x, y, width });
      x += width + 6;
    });
    return {
      placements,
      height: placements.length ? placements.at(-1).y + 24 : 0
    };
  }

  function measureWeeklyRobotCardHeight(ctx, robot, cardWidth) {
    setReportFont(ctx, 600, 12);
    const tagLayout = weeklyRobotTagLayout(ctx, robot.problems, cardWidth - 122);
    return Math.max(210, 142 + tagLayout.height);
  }

  function drawWeeklyRobotDetailCard(ctx, card) {
    fillRoundedRect(ctx, card.x, card.y, card.width, card.height, 8, reportTheme.surface);
    strokeRoundedRect(ctx, card.x, card.y, card.width, card.height, 8, reportTheme.line);
    ctx.fillStyle = weeklyRobotRiskColor(card.robot.risk);
    ctx.fillRect(card.x, card.y, card.width, 4);

    setReportFont(ctx, 800, 25);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(card.robot.robot, card.x + 18, card.y + 34);
    const riskLabel = robotRiskLabel(card.robot.risk);
    setReportFont(ctx, 700, 13);
    const riskWidth = ctx.measureText(riskLabel).width + 18;
    fillRoundedRect(
      ctx,
      card.x + card.width - riskWidth - 16,
      card.y + 14,
      riskWidth,
      25,
      5,
      card.robot.risk === "highRisk" ? reportTheme.redSoft : card.robot.risk === "attention" ? reportTheme.amberSoft : reportTheme.blueSoft
    );
    ctx.fillStyle = weeklyRobotRiskColor(card.robot.risk);
    ctx.fillText(riskLabel, card.x + card.width - riskWidth - 7, card.y + 31);

    setReportFont(ctx, 700, 13);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText("本期问题", card.x + 18, card.y + 73);
    setReportFont(ctx, 800, 25);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(`${card.robot.count} 次`, card.x + 18, card.y + 104);

    const tagX = card.x + 104;
    const tagWidth = card.width - 122;
    setReportFont(ctx, 700, 13);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText("问题明细", tagX, card.y + 73);
    setReportFont(ctx, 600, 12);
    const tagLayout = weeklyRobotTagLayout(ctx, card.robot.problems, tagWidth);
    tagLayout.placements.forEach((tag) => {
      const style = weeklyProblemTagStyle(issueLevelForCount(tag.problem.count));
      fillRoundedRect(ctx, tagX + tag.x, card.y + 84 + tag.y, tag.width, 24, 5, style.bg);
      ctx.fillStyle = style.text;
      ctx.fillText(
        truncateCanvasText(ctx, tag.label, tag.width - 14),
        tagX + tag.x + 7,
        card.y + 100 + tag.y
      );
    });

    const footerY = card.y + card.height - 44;
    ctx.strokeStyle = reportTheme.line;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(card.x, footerY);
    ctx.lineTo(card.x + card.width, footerY);
    ctx.stroke();
    const { latestIssue } = robotCardPresentation(card.robot, card.period);
    setReportFont(ctx, 700, 12);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText(latestIssue ? "最新事件" : "重点问题", card.x + 16, footerY + 27);
    setReportFont(ctx, 700, 13);
    ctx.fillStyle = reportTheme.ink;
    const footerText = latestIssue
      ? `${latestIssue.occurredAt.slice(5, 16).replace("T", " ")}  ${latestIssue.symptom}`
      : card.robot.problems[0]?.issue || "暂无";
    ctx.fillText(
      truncateCanvasText(ctx, footerText, card.width - 105),
      card.x + 90,
      footerY + 27
    );
  }

  function weeklyProblemTagStyle(level) {
    return {
      serious: { bg: reportTheme.redSoft, text: reportTheme.red },
      warning: { bg: reportTheme.amberSoft, text: reportTheme.amber },
      normal: { bg: reportTheme.blueSoft, text: reportTheme.blue }
    }[level] || { bg: reportTheme.blueSoft, text: reportTheme.blue };
  }

  function truncateCanvasText(ctx, text, maxWidth) {
    const value = String(text || "");
    if (ctx.measureText(value).width <= maxWidth) return value;
    let result = value;
    while (result.length && ctx.measureText(`${result}…`).width > maxWidth) {
      result = result.slice(0, -1);
    }
    return `${result}…`;
  }

  function openReport() {
    els.reportText.value = buildReport(state.activeDate);
    renderReportImage(state.activeDate);
    els.reportDialog.showModal();
    els.copyReportImageBtn.focus();
  }

  async function copyReportText() {
    const text = els.reportText.value || buildReport(state.activeDate);
    try {
      await navigator.clipboard.writeText(text);
      showToast("日报文本已复制");
    } catch (error) {
      const details = els.reportText.closest("details");
      if (details) {
        details.open = true;
      }
      els.reportText.focus();
      els.reportText.select();
      showToast("已选中文本，可手动复制");
    }
  }

  async function copyReportImage() {
    if (!els.reportCanvas.width || !els.reportCanvas.height) {
      renderReportImage(state.activeDate);
    }

    const blob = await canvasToPngBlob(els.reportCanvas);
    if (!blob) {
      showToast("日报图片生成失败");
      return;
    }

    if (navigator.clipboard && window.ClipboardItem) {
      try {
        await navigator.clipboard.write([
          new ClipboardItem({
            "image/png": blob
          })
        ]);
        showToast("日报图片已复制");
        return;
      } catch (error) {
        console.warn("Failed to copy report image", error);
      }
    }

    downloadBlob(reportImageFilename(), blob);
    showToast("浏览器不支持直接复制图片，已下载 PNG");
  }

  async function downloadReportImage() {
    if (!els.reportCanvas.width || !els.reportCanvas.height) {
      renderReportImage(state.activeDate);
    }

    const blob = await canvasToPngBlob(els.reportCanvas);
    if (!blob) {
      showToast("日报图片生成失败");
      return;
    }

    downloadBlob(reportImageFilename(), blob);
    showToast("日报图片已下载");
  }

  function renderReportImage(date) {
    const canvas = els.reportCanvas;
    const report = getReportImageData(date);
    const width = 1280;
    const scale = 2;
    const measureCanvas = document.createElement("canvas");
    const measureCtx = measureCanvas.getContext("2d");
    const layout = buildReportImageLayout(measureCtx, report, width);
    const ctx = canvas.getContext("2d");

    canvas.width = layout.width * scale;
    canvas.height = layout.height * scale;
    canvas.style.aspectRatio = `${layout.width} / ${layout.height}`;
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    drawReportImage(ctx, report, layout);
  }

  function getReportImageData(date) {
    const summary = getSummary(date);
    const rate = summary.total ? Math.round((summary.normal / summary.total) * 100) : 0;
    const rows = state.store.robots.map((robot) => {
      const snapshot = getRecord(date, robot.id);
      const issues = getOpenIssuesForRobot(robot.id).sort(compareIssuePriority);
      const primaryIssue = issues[0];
      const record = {
        ...snapshot,
        severity: primaryIssue?.priority || "low",
        owner: primaryIssue?.owner || "",
        nextAction: primaryIssue?.nextAction || ""
      };
      return {
        robot,
        record,
        primaryIssue,
        note: issues.length
          ? issues.map((issue) => `${categoryLabel(issue.systemCategory)}：${issue.symptom}`).join("；")
          : "无未关闭问题"
      };
    });

    return {
      date,
      summary,
      rate,
      rows,
      focusItems: rows
        .filter(({ primaryIssue }) => Boolean(primaryIssue))
        .sort((a, b) => compareIssuePriority(a.primaryIssue, b.primaryIssue))
        .map((item) => ({
          ...item,
          detail: compactText([
            item.primaryIssue.symptom,
            item.primaryIssue.owner && `负责人：${item.primaryIssue.owner}`,
            item.primaryIssue.nextAction && `下一步：${item.primaryIssue.nextAction}`
          ])
        }))
    };
  }

  function buildReportImageLayout(ctx, report, width) {
    const margin = 48;
    const contentWidth = width - margin * 2;
    const tableColumns = getReportTableColumns(contentWidth);
    let y = 40;

    const headerY = y;
    const headerHeight = 118;
    y += headerHeight + 24;

    const metricsY = y;
    const metricsHeight = 124;
    y += metricsHeight + 32;

    const focusTitleY = y;
    y += 38 + 12;

    const focusItemsY = y;
    const focusItemHeights = report.focusItems.length
      ? report.focusItems.map((item) => {
        setReportFont(ctx, 400, 22);
        const lineCount = wrapCanvasText(ctx, item.detail, contentWidth - 44).length;
        return Math.max(84, 58 + lineCount * 26);
      })
      : [64];
    y += focusItemHeights.reduce((total, height) => total + height, 0);
    y += Math.max(0, focusItemHeights.length - 1) * 12 + 30;

    const tableTitleY = y;
    y += 38 + 12;

    const tableY = y;
    const tableHeaderHeight = 46;
    y += tableHeaderHeight;

    setReportFont(ctx, 400, 19);
    const noteColumn = tableColumns[tableColumns.length - 1];
    const tableRowHeights = report.rows.map((row) => {
      const lineCount = wrapCanvasText(ctx, row.note, noteColumn.width - 24).length;
      return Math.max(64, 30 + lineCount * 24);
    });
    y += tableRowHeights.reduce((total, height) => total + height, 0);

    const footerY = y + 22;
    y = footerY + 26 + 44;

    return {
      width,
      height: Math.ceil(y),
      margin,
      contentWidth,
      headerY,
      headerHeight,
      metricsY,
      metricsHeight,
      focusTitleY,
      focusItemsY,
      focusItemHeights,
      tableTitleY,
      tableY,
      tableHeaderHeight,
      tableColumns,
      tableRowHeights,
      footerY
    };
  }

  function drawReportImage(ctx, report, layout) {
    ctx.clearRect(0, 0, layout.width, layout.height);
    ctx.fillStyle = reportTheme.bg;
    ctx.fillRect(0, 0, layout.width, layout.height);

    drawReportHeader(ctx, report, layout);
    drawReportMetrics(ctx, report, layout);
    drawReportFocus(ctx, report, layout);
    drawReportTable(ctx, report, layout);
    drawReportFooter(ctx, layout);
  }

  function drawReportHeader(ctx, report, layout) {
    fillRoundedRect(ctx, layout.margin, layout.headerY, layout.contentWidth, layout.headerHeight, 18, reportTheme.ink);

    setReportFont(ctx, 700, 22);
    ctx.fillStyle = "rgba(255,255,255,0.72)";
    ctx.fillText("现场机器人日常记录", layout.margin + 34, layout.headerY + 40);

    setReportFont(ctx, 800, 42);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(`${report.date} 现场机器人日报`, layout.margin + 34, layout.headerY + 88);

    const summary = `总数 ${report.summary.total} · 正常 ${report.summary.normal} · 停用 ${report.summary.disabled} · 未关闭问题 ${report.summary.openIssueCount}`;
    setReportFont(ctx, 700, 20);
    ctx.fillStyle = "rgba(255,255,255,0.86)";
    drawTextRight(ctx, summary, layout.margin + layout.contentWidth - 34, layout.headerY + 44);

    setReportFont(ctx, 800, 48);
    ctx.fillStyle = reportTheme.greenSoft;
    drawTextRight(ctx, `${report.rate}%`, layout.margin + layout.contentWidth - 34, layout.headerY + 94);
  }

  function drawReportMetrics(ctx, report, layout) {
    const cards = statusMetricCards(report.summary).slice(0, 4);
    const gap = 14;
    const cardWidth = (layout.contentWidth - gap * 3) / 4;

    cards.forEach(({ label, value, helper, color }, index) => {
      const x = layout.margin + index * (cardWidth + gap);
      fillRoundedRect(ctx, x, layout.metricsY, cardWidth, layout.metricsHeight, 12, reportTheme.surface);
      strokeRoundedRect(ctx, x, layout.metricsY, cardWidth, layout.metricsHeight, 12, reportTheme.line);
      ctx.fillStyle = color;
      ctx.fillRect(x, layout.metricsY, cardWidth, 5);

      setReportFont(ctx, 800, 19);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText(label, x + 20, layout.metricsY + 34);

      setReportFont(ctx, 800, 42);
      ctx.fillStyle = reportTheme.ink;
      ctx.fillText(value, x + 20, layout.metricsY + 82);

      setReportFont(ctx, 600, 18);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText(helper, x + 20, layout.metricsY + 108);
    });
  }

  function drawReportFocus(ctx, report, layout) {
    drawSectionTitle(ctx, "今日重点", `${report.focusItems.length} 项`, layout.margin, layout.focusTitleY, layout.contentWidth);
    let y = layout.focusItemsY;

    if (!report.focusItems.length) {
      fillRoundedRect(ctx, layout.margin, y, layout.contentWidth, layout.focusItemHeights[0], 12, reportTheme.surface);
      strokeRoundedRect(ctx, layout.margin, y, layout.contentWidth, layout.focusItemHeights[0], 12, reportTheme.line);
      setReportFont(ctx, 700, 22);
      ctx.fillStyle = reportTheme.muted;
      ctx.fillText("当前没有重点项", layout.margin + 22, y + 40);
      return;
    }

    report.focusItems.forEach((item, index) => {
      const height = layout.focusItemHeights[index];
      const accent = focusAccentColor(item.record);
      fillRoundedRect(ctx, layout.margin, y, layout.contentWidth, height, 12, reportTheme.surface);
      strokeRoundedRect(ctx, layout.margin, y, layout.contentWidth, height, 12, reportTheme.line);
      ctx.fillStyle = accent;
      ctx.fillRect(layout.margin, y + 12, 5, height - 24);

      setReportFont(ctx, 800, 23);
      ctx.fillStyle = reportTheme.ink;
      const titleMaxWidth = layout.contentWidth - 410;
      ctx.fillText(clipCanvasText(ctx, `${item.robot.code} ${item.robot.name}`, titleMaxWidth), layout.margin + 22, y + 34);

      let badgeX = layout.margin + layout.contentWidth - 22;
      badgeX = drawBadgeRight(ctx, labelFor(availabilityOptions, item.record.availability), badgeX, y + 17, statusStyle(item.record.availability));
      badgeX = drawBadgeRight(ctx, labelFor(severityOptions, item.record.severity), badgeX - 8, y + 17, severityStyle(item.record.severity));
      drawBadgeRight(ctx, labelFor(repairOptions, item.record.repairStatus), badgeX - 8, y + 17, neutralBadgeStyle());

      setReportFont(ctx, 400, 22);
      ctx.fillStyle = reportTheme.muted;
      drawWrappedText(ctx, item.detail, layout.margin + 22, y + 64, layout.contentWidth - 44, 26);

      y += height + 12;
    });
  }

  function drawReportTable(ctx, report, layout) {
    drawSectionTitle(ctx, "全部状态", `${report.rows.length} 台机器人`, layout.margin, layout.tableTitleY, layout.contentWidth);

    let x = layout.margin;
    const headerY = layout.tableY;
    fillRoundedRect(ctx, layout.margin, headerY, layout.contentWidth, layout.tableHeaderHeight, 12, reportTheme.ink);
    ctx.save();
    ctx.beginPath();
    ctx.rect(layout.margin, headerY + 12, layout.contentWidth, layout.tableHeaderHeight - 12);
    ctx.clip();
    ctx.fillStyle = reportTheme.ink;
    ctx.fillRect(layout.margin, headerY, layout.contentWidth, layout.tableHeaderHeight);
    ctx.restore();

    setReportFont(ctx, 800, 18);
    ctx.fillStyle = "#ffffff";
    layout.tableColumns.forEach((column) => {
      ctx.fillText(column.label, x + 12, headerY + 30);
      x += column.width;
    });

    let y = headerY + layout.tableHeaderHeight;
    report.rows.forEach((row, index) => {
      const height = layout.tableRowHeights[index];
      const rowBg = index % 2 === 0 ? reportTheme.surface : reportTheme.surfaceSoft;
      ctx.fillStyle = rowBg;
      ctx.fillRect(layout.margin, y, layout.contentWidth, height);
      ctx.strokeStyle = reportTheme.line;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(layout.margin, y + height);
      ctx.lineTo(layout.margin + layout.contentWidth, y + height);
      ctx.stroke();

      x = layout.margin;
      drawTableRobotCell(ctx, row, x + 12, y, layout.tableColumns[0].width - 24);
      x += layout.tableColumns[0].width;
      drawBadge(ctx, labelFor(availabilityOptions, row.record.availability), x + 12, y + 18, statusStyle(row.record.availability));
      x += layout.tableColumns[1].width;
      drawBadge(ctx, labelFor(repairOptions, row.record.repairStatus), x + 12, y + 18, neutralBadgeStyle());
      x += layout.tableColumns[2].width;
      drawBadge(ctx, labelFor(severityOptions, row.record.severity), x + 12, y + 18, severityStyle(row.record.severity));
      x += layout.tableColumns[3].width;

      setReportFont(ctx, 700, 18);
      ctx.fillStyle = row.record.owner.trim() ? reportTheme.ink : reportTheme.muted;
      ctx.fillText(clipCanvasText(ctx, row.record.owner.trim() || "-", layout.tableColumns[4].width - 24), x + 12, y + 39);
      x += layout.tableColumns[4].width;

      setReportFont(ctx, 400, 19);
      ctx.fillStyle = row.note === "无异常记录" ? reportTheme.muted : reportTheme.ink;
      drawWrappedText(ctx, row.note, x + 12, y + 27, layout.tableColumns[5].width - 24, 24);

      y += height;
    });

    strokeRoundedRect(ctx, layout.margin, layout.tableY, layout.contentWidth, y - layout.tableY, 12, reportTheme.line);
  }

  function drawReportFooter(ctx, layout) {
    setReportFont(ctx, 600, 17);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText("由本地记录生成，未连接网络", layout.margin, layout.footerY);
    drawTextRight(ctx, `生成时间 ${formatDateTime(new Date())}`, layout.margin + layout.contentWidth, layout.footerY);
  }

  function drawSectionTitle(ctx, title, meta, x, y, width) {
    setReportFont(ctx, 800, 28);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(title, x, y + 29);

    setReportFont(ctx, 800, 18);
    ctx.fillStyle = reportTheme.muted;
    drawTextRight(ctx, meta, x + width, y + 28);
  }

  function drawTableRobotCell(ctx, row, x, y, maxWidth) {
    setReportFont(ctx, 800, 20);
    ctx.fillStyle = reportTheme.ink;
    ctx.fillText(clipCanvasText(ctx, row.robot.code, maxWidth), x, y + 28);

    setReportFont(ctx, 600, 17);
    ctx.fillStyle = reportTheme.muted;
    ctx.fillText(clipCanvasText(ctx, row.robot.name, maxWidth), x, y + 52);
  }

  function getReportTableColumns(contentWidth) {
    const columns = [
      { key: "robot", label: "机器人", width: 196 },
      { key: "status", label: "可用状态", width: 132 },
      { key: "repair", label: "维修状态", width: 128 },
      { key: "severity", label: "优先级", width: 110 },
      { key: "owner", label: "负责人", width: 116 }
    ];
    const fixedWidth = columns.reduce((total, column) => total + column.width, 0);
    columns.push({ key: "note", label: "问题 / 进展 / 下一步", width: contentWidth - fixedWidth });
    return columns;
  }

  function focusAccentColor(record) {
    if (record.severity === "urgent" || record.severity === "high" || record.availability === "disabled") {
      return reportTheme.red;
    }
    if (record.availability === "normal") {
      return reportTheme.green;
    }
    return reportTheme.blue;
  }

  function statusStyle(status) {
    const styles = {
      normal: { bg: reportTheme.greenSoft, fg: reportTheme.green },
      disabled: { bg: reportTheme.redSoft, fg: reportTheme.red }
    };
    return styles[status] || neutralBadgeStyle();
  }

  function severityStyle(severity) {
    if (severity === "high" || severity === "urgent") {
      return { bg: reportTheme.redSoft, fg: reportTheme.red };
    }
    if (severity === "medium") {
      return { bg: reportTheme.amberSoft, fg: reportTheme.amber };
    }
    return { bg: reportTheme.graySoft, fg: reportTheme.muted };
  }

  function neutralBadgeStyle() {
    return { bg: reportTheme.graySoft, fg: reportTheme.muted };
  }

  function drawBadge(ctx, text, x, y, style) {
    setReportFont(ctx, 800, 16);
    const width = Math.ceil(ctx.measureText(text).width) + 24;
    fillRoundedRect(ctx, x, y, width, 28, 14, style.bg);
    ctx.fillStyle = style.fg;
    ctx.fillText(text, x + 12, y + 20);
    return width;
  }

  function drawBadgeRight(ctx, text, rightX, y, style) {
    setReportFont(ctx, 800, 16);
    const width = Math.ceil(ctx.measureText(text).width) + 24;
    drawBadge(ctx, text, rightX - width, y, style);
    return rightX - width;
  }

  function drawWrappedText(ctx, text, x, y, maxWidth, lineHeight) {
    wrapCanvasText(ctx, text, maxWidth).forEach((line, index) => {
      ctx.fillText(line, x, y + index * lineHeight);
    });
  }

  function wrapCanvasText(ctx, text, maxWidth) {
    const paragraphs = String(text || "").split(/\n+/).map((part) => part.trim()).filter(Boolean);
    const source = paragraphs.length ? paragraphs : [""];
    const lines = [];

    source.forEach((paragraph) => {
      let line = "";
      Array.from(paragraph).forEach((char) => {
        const next = line + char;
        if (line && ctx.measureText(next).width > maxWidth) {
          lines.push(line);
          line = char.trimStart();
        } else {
          line = next;
        }
      });
      lines.push(line);
    });

    return lines;
  }

  function clipCanvasText(ctx, text, maxWidth) {
    const clean = String(text || "");
    if (ctx.measureText(clean).width <= maxWidth) {
      return clean;
    }

    let clipped = clean;
    while (clipped.length > 1 && ctx.measureText(`${clipped}…`).width > maxWidth) {
      clipped = clipped.slice(0, -1);
    }
    return `${clipped}…`;
  }

  function drawTextRight(ctx, text, rightX, y) {
    ctx.fillText(text, rightX - ctx.measureText(text).width, y);
  }

  function setReportFont(ctx, weight, size) {
    ctx.font = `${weight} ${size}px -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif`;
    ctx.textBaseline = "alphabetic";
  }

  function fillRoundedRect(ctx, x, y, width, height, radius, color) {
    roundedRectPath(ctx, x, y, width, height, radius);
    ctx.fillStyle = color;
    ctx.fill();
  }

  function strokeRoundedRect(ctx, x, y, width, height, radius, color) {
    roundedRectPath(ctx, x, y, width, height, radius);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function roundedRectPath(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function canvasToPngBlob(canvas) {
    return new Promise((resolve) => {
      canvas.toBlob(resolve, "image/png");
    });
  }

  function reportImageFilename() {
    return `robot-report-${state.activeDate}.png`;
  }

  function exportCurrentCsv() {
    const headers = ["日期", "编号", "名称", "型号", "位置", "可用状态", "维修状态", "未关闭问题数", "影响采集问题数", "当前问题", "问题分类", "处理阶段", "负责人", "下一步", "计划完成"];
    const rows = state.store.robots.map((robot) => {
      const record = getRecord(state.activeDate, robot.id);
      const issues = getOpenIssuesForRobot(robot.id).sort(compareIssuePriority);
      const primaryIssue = issues[0];
      return [
        state.activeDate,
        robot.code,
        robot.name,
        robot.model,
        robot.location,
        labelFor(availabilityOptions, record.availability),
        labelFor(repairOptions, record.repairStatus),
        issues.length,
        issues.filter((issue) => issue.impactsCollection).length,
        primaryIssue?.symptom || "",
        primaryIssue ? categoryLabel(primaryIssue.systemCategory) : "",
        primaryIssue ? labelFor(issueStatusOptions, primaryIssue.status) : "",
        primaryIssue?.owner || "",
        primaryIssue?.nextAction || "",
        primaryIssue?.dueDate || ""
      ];
    });
    const csv = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
    downloadFile(`robot-record-${state.activeDate}.csv`, `\uFEFF${csv}`, "text/csv;charset=utf-8");
    showToast("CSV 已导出");
  }

  function closeExportMenu() {
    els.exportMenu.open = false;
  }

  function exportJson() {
    const payload = JSON.stringify(state.store, null, 2);
    downloadFile(`robot-record-${currentProject()?.id || "project"}-${state.activeDate}.json`, payload, "application/json;charset=utf-8");
    showToast(`“${currentProject()?.name || "当前项目"}”JSON 备份已导出`);
  }

  function importJson(event) {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (!file) {
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || ""));
        if (!parsed || !Array.isArray(parsed.robots) || typeof parsed.records !== "object") {
          throw new Error("Invalid backup format");
        }
        if (!window.confirm(`导入 JSON 会替换“${currentProject()?.name || "当前项目"}”的台账数据，其他项目不受影响。确定继续？`)) {
          return;
        }
        state.store = normalizeStore(parsed);
        delete state.store._needsMigrationSave;
        saveStore();
        renderAll();
        showToast(`JSON 已导入“${currentProject()?.name || "当前项目"}”`);
      } catch (error) {
        console.error(error);
        showToast("JSON 格式不正确");
      }
    };
    reader.readAsText(file);
  }

  function getSummary(date) {
    const robotStates = state.store.robots.map((robot) => ({ robot, record: getRecord(date, robot.id) }));
    const records = robotStates.map(({ record }) => record);
    const summary = records.reduce((result, record) => {
      result.total += 1;
      result[record.availability] = (result[record.availability] || 0) + 1;
      return result;
    }, {
      total: 0,
      normal: 0,
      disabled: 0
    });
    summary.normalRobotCodes = robotStates
      .filter(({ record }) => record.availability === "normal")
      .map(({ robot }) => robot.code);
    for (const repairStatus of ["repairing", "afterSales", "awayRepair"]) {
      summary[`${repairStatus}RobotCodes`] = robotStates
        .filter(({ record }) => record.availability === "disabled" && record.repairStatus === repairStatus)
        .map(({ robot }) => robot.code);
      summary[repairStatus] = summary[`${repairStatus}RobotCodes`].length;
    }
    const openIssues = getOpenIssues();
    summary.openIssueCount = openIssues.length;
    summary.issueRobotCount = new Set(openIssues.map((issue) => issue.robotId)).size;
    summary.impactingIssueCount = openIssues.filter((issue) => issue.impactsCollection).length;
    summary.todayIssueCount = state.store.issues.filter((issue) => issue.occurredAt.slice(0, 10) === date).length;
    summary.overdueIssueCount = openIssues.filter(isIssueOverdue).length;
    return summary;
  }

  function getTrendPeriod(dateString, range) {
    return recordData.resolveTrendPeriod(
      dateString,
      range,
      range === "custom" ? els.trendCustomStart.value : "",
      range === "custom" ? els.trendCustomEnd.value : ""
    );
  }

  function getRecord(date, robotId) {
    const resolved = recordData.resolveRecordAtDate(state.store.records, date, robotId);
    return normalizeRecord(resolved?.record);
  }

  function ensureRecord(date, robotId) {
    if (!state.store.records[date]) {
      state.store.records[date] = {};
    }
    if (!state.store.records[date][robotId]) {
      state.store.records[date][robotId] = getRecord(date, robotId);
    } else {
      state.store.records[date][robotId] = normalizeRecord(state.store.records[date][robotId]);
    }
    return state.store.records[date][robotId];
  }

  function normalizeRecordStore(records) {
    if (!records || typeof records !== "object") {
      return {};
    }
    return Object.fromEntries(Object.entries(records).map(([date, dailyRecords]) => [
      date,
      Object.fromEntries(Object.entries(dailyRecords || {}).map(([robotId, record]) => [
        robotId,
        normalizeRecord(record)
      ]))
    ]));
  }

  function normalizeAvailability(value) {
    if (value === "disabled" || value === "repair" || value === "offline") {
      return "disabled";
    }
    return "normal";
  }

  function normalizeRepairStatus(value) {
    if (value === "diagnosing") return "diagnosing";
    if (value === "awayRepair" || value === "away" || value === "offsite" || value === "sentForRepair") return "awayRepair";
    if (value === "afterSales" || value === "waitingAfterSales" || value === "waitingParts") return "afterSales";
    if (value === "repairing" || value === "testing") return "repairing";
    return "none";
  }

  function normalizeRecord(record = {}) {
    return {
      ...defaultRecord,
      ...record,
      availability: normalizeAvailability(record.availability),
      repairStatus: normalizeRepairStatus(record.repairStatus),
      severity: optionValueOrDefault(
        severityOptions,
        record.severity === "critical" ? "urgent" : record.severity,
        defaultRecord.severity
      ),
      softwareIssue: String(record.softwareIssue || ""),
      hardwareIssue: String(record.hardwareIssue || ""),
      repairProgress: String(record.repairProgress || ""),
      nextAction: String(record.nextAction || ""),
      owner: String(record.owner || ""),
      updatedAt: String(record.updatedAt || "")
    };
  }

  function normalizeIssues(issues, robots, catalog) {
    const seenIds = new Set();
    return (Array.isArray(issues) ? issues : []).slice(0, 50000).map((issue, index) => {
      const normalized = normalizeIssue(issue, robots, catalog);
      if (!normalized.id || seenIds.has(normalized.id)) {
        normalized.id = `ISSUE-IMPORTED-${String(index + 1).padStart(5, "0")}`;
      }
      seenIds.add(normalized.id);
      return normalized;
    });
  }

  function normalizeIssue(issue = {}, robots = [], catalog) {
    const robot = robots.find((candidate) => candidate.id === issue.robotId);
    const occurredAt = String(issue.occurredAt || issue.createdAt || new Date().toISOString());
    const status = optionValueOrDefault(issueStatusOptions, issue.status, "open");
    const classified = problemData.manualIssueOccurrences([{ ...issue, occurredAt }], robots, catalog)[0]
      || problemData.normalizeProblemOccurrence({ rawIssue: issue.symptom, occurredAt }, { classificationCatalog: catalog });
    return {
      id: String(issue.id || ""),
      robotId: String(issue.robotId || ""),
      robotCode: String(issue.robotCode || robot?.code || "未知设备"),
      robotName: String(issue.robotName || robot?.name || ""),
      occurredAt,
      rawIssue: String(issue.rawIssue || issue.symptom || "未填写现场现象"),
      faultName: String(issue.faultName || classified.faultName),
      systemCategory: classified.systemCategory,
      moduleCategory: classified.moduleCategory,
      faultSeverity: classified.severity,
      classificationSource: String(issue.classificationSource || classified.classificationSource || "rules"),
      symptom: String(issue.symptom || "未填写现场现象"),
      impactsCollection: Boolean(issue.impactsCollection),
      priority: optionValueOrDefault(severityOptions, classified.priority, "medium"),
      isP0: Boolean(issue.isP0),
      owner: String(issue.owner || ""),
      status,
      nextAction: String(issue.nextAction || ""),
      dueDate: String(issue.dueDate || "").slice(0, 10),
      recoveredAt: String(issue.recoveredAt || ""),
      closedAt: String(issue.closedAt || ""),
      rootCause: String(issue.rootCause || ""),
      resolution: String(issue.resolution || ""),
      source: issue.source === "legacy" ? "legacy" : "manual",
      createdAt: String(issue.createdAt || occurredAt),
      updatedAt: String(issue.updatedAt || issue.createdAt || occurredAt),
      timeline: normalizeIssueTimeline(issue.timeline, occurredAt)
    };
  }

  function normalizeIssueTimeline(timeline, fallbackAt) {
    const entries = (Array.isArray(timeline) ? timeline : []).slice(0, 500).map((entry, index) => ({
      id: String(entry?.id || `timeline-${index + 1}`),
      at: String(entry?.at || fallbackAt),
      type: String(entry?.type || "update"),
      note: String(entry?.note || "处理记录")
    }));
    return entries.length ? entries : [{
      id: "timeline-created",
      at: fallbackAt,
      type: "created",
      note: "问题已记录"
    }];
  }

  function migrateLegacyIssues(records, robots, catalog) {
    const issues = [];
    Object.keys(records).sort().forEach((date) => {
      Object.entries(records[date] || {}).forEach(([robotId, recordValue]) => {
        const record = normalizeRecord(recordValue);
        const robot = robots.find((candidate) => candidate.id === robotId);
        [
          ["softwareIssue", "software", "SOFTWARE"],
          ["hardwareIssue", "hardware", "HARDWARE"]
        ].forEach(([field, category, suffix]) => {
          const symptom = record[field].trim();
          if (!symptom) return;
          const active = record.availability === "disabled" || record.repairStatus !== "none";
          const occurredAt = `${date}T09:00:00`;
          const updatedAt = record.updatedAt || occurredAt;
          const progress = compactText([
            record.repairProgress,
            record.nextAction && `下一步：${record.nextAction}`
          ]);
          issues.push(normalizeIssue({
            id: `LEGACY-${date.replace(/-/g, "")}-${String(robot?.code || robotId).replace(/[^a-zA-Z0-9]/g, "")}-${suffix}`,
            robotId,
            robotCode: robot?.code || robotId,
            robotName: robot?.name || "",
            occurredAt,
            category,
            symptom,
            impactsCollection: record.availability === "disabled",
            severity: record.severity,
            owner: record.owner,
            status: active ? "open" : "closed",
            nextAction: record.nextAction,
            recoveredAt: active ? "" : updatedAt,
            closedAt: active ? "" : updatedAt,
            resolution: record.repairProgress,
            source: "legacy",
            createdAt: updatedAt,
            updatedAt,
            timeline: [{
              id: `legacy-${date}-${robotId}-${suffix}`,
              at: updatedAt,
              type: "migrated",
              note: progress || "由旧版每日台账迁移"
            }]
          }, robots, catalog));
        });
      });
    });
    return issues;
  }

  function getIssueById(issueId) {
    return state.store.issues.find((issue) => issue.id === issueId);
  }

  function getOpenIssues() {
    return state.store.issues.filter((issue) => issue.status !== "closed");
  }

  function getOpenIssuesForRobot(robotId) {
    return getOpenIssues().filter((issue) => issue.robotId === robotId);
  }

  function robotForIssue(issue) {
    return state.store.robots.find((robot) => robot.id === issue.robotId) || {
      id: issue.robotId,
      code: issue.robotCode || "未知设备",
      name: issue.robotName || "已移出清单"
    };
  }

  function isIssueOverdue(issue) {
    return issue.status !== "closed" && Boolean(issue.dueDate) && issue.dueDate < toLocalDateString(new Date());
  }

  function compareIssuePriority(a, b) {
    const severityRank = { low: 1, medium: 2, high: 3, urgent: 4 };
    return (
      Number(b.isP0) - Number(a.isP0) ||
      Number(b.impactsCollection) - Number(a.impactsCollection) ||
      Number(isIssueOverdue(b)) - Number(isIssueOverdue(a)) ||
      (severityRank[b.priority] || 0) - (severityRank[a.priority] || 0) ||
      String(a.dueDate || "9999-12-31").localeCompare(String(b.dueDate || "9999-12-31")) ||
      b.occurredAt.localeCompare(a.occurredAt)
    );
  }

  function appendIssueTimeline(issue, type, note, at = new Date().toISOString()) {
    issue.timeline.push({
      id: createTimelineId(),
      at,
      type,
      note
    });
    issue.updatedAt = at;
  }

  function createTimelineId() {
    if (window.crypto?.randomUUID) return `event-${window.crypto.randomUUID()}`;
    return `event-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }

  function createIssueId(occurredAt) {
    const datePart = String(occurredAt).slice(0, 10).replace(/-/g, "") || toLocalDateString(new Date()).replace(/-/g, "");
    const prefix = `ISSUE-${datePart}-`;
    const sequence = state.store.issues.reduce((max, issue) => {
      if (!issue.id.startsWith(prefix)) return max;
      return Math.max(max, Number(issue.id.slice(prefix.length)) || 0);
    }, 0) + 1;
    return `${prefix}${String(sequence).padStart(3, "0")}`;
  }

  function timelineTypeLabel(type) {
    return {
      created: "问题创建",
      migrated: "历史迁移",
      update: "处理进展",
      status: "信息变更",
      recovered: "已恢复",
      closed: "验证关闭"
    }[type] || "处理记录";
  }

  function formatIssueAge(issue) {
    const start = new Date(issue.occurredAt);
    const end = issue.closedAt ? new Date(issue.closedAt) : new Date();
    const milliseconds = Math.max(0, end.getTime() - start.getTime());
    if (!Number.isFinite(milliseconds)) return "—";
    const hours = Math.floor(milliseconds / 3600000);
    if (hours < 1) return "不足 1 小时";
    if (hours < 48) return `${hours} 小时`;
    return `${Math.floor(hours / 24)} 天`;
  }

  function defaultIssueDateTime() {
    const now = new Date();
    const date = state.activeDate || toLocalDateString(now);
    const time = date === toLocalDateString(now)
      ? `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`
      : "09:00";
    return `${date}T${time}`;
  }

  function normalizeDateTimeValue(value) {
    const text = String(value || "");
    return text.length === 16 ? `${text}:00` : text;
  }

  function formatDateTimeValue(value) {
    const text = String(value || "");
    if (!text) return "—";
    if (/Z$|[+-]\d{2}:\d{2}$/.test(text)) {
      const date = new Date(text);
      return Number.isNaN(date.getTime()) ? text : formatDateTime(date);
    }
    return text.slice(0, 16).replace("T", " ");
  }

  function buildReport(date) {
    const summary = getSummary(date);
    const rate = summary.total ? Math.round((summary.normal / summary.total) * 100) : 0;
    const todayIssues = state.store.issues
      .filter((issue) => issue.occurredAt.slice(0, 10) === date)
      .sort(compareIssuePriority);
    const openIssues = getOpenIssues().sort(compareIssuePriority);

    const lines = [
      `${date} 现场机器人日报`,
      `总数：${summary.total}，正常：${summary.normal}，停用：${summary.disabled}，正常率：${rate}%`,
      `维修状态：${repairSummaryText(summary)}`,
      `未关闭问题：${summary.openIssueCount}项，涉及${summary.issueRobotCount}台；影响采集：${summary.impactingIssueCount}项`,
      `今日新增：${summary.todayIssueCount}项；动作逾期：${summary.overdueIssueCount}项`,
      "",
      "今日新增问题："
    ];

    if (!todayIssues.length) {
      lines.push("- 无新增问题");
    } else {
      todayIssues.forEach((issue) => {
        const robot = robotForIssue(issue);
        lines.push(`- ${robot.code} ${categoryLabel(issue.systemCategory)}：${issue.symptom}（${labelFor(issueStatusOptions, issue.status)}${issue.impactsCollection ? "，影响采集" : ""}）`);
      });
    }

    lines.push("", "未关闭问题：");
    if (!openIssues.length) {
      lines.push("- 无未关闭问题");
    } else {
      openIssues.forEach((issue) => {
        const robot = robotForIssue(issue);
        lines.push(`- ${robot.code} ${issue.id}：${issue.symptom}`);
        lines.push(`  阶段：${labelFor(issueStatusOptions, issue.status)}；负责人：${issue.owner || "未分配"}；下一步：${issue.nextAction || "未填写"}`);
      });
    }

    lines.push("", "全部设备状态：");
    state.store.robots.forEach((robot) => {
      const record = getRecord(date, robot.id);
      lines.push(`- ${robot.code} ${robot.name}：${labelFor(availabilityOptions, record.availability)}，维修：${labelFor(repairOptions, record.repairStatus)}`);
    });

    return lines.join("\n");
  }

  function repairSummaryText(summary) {
    return `维修中 ${summary.repairing} · 待售后 ${summary.afterSales} · 离场 ${summary.awayRepair}`;
  }

  function selectHtml(robotId, field, current, options) {
    return `
      <select data-robot-id="${h(robotId)}" data-field="${h(field)}">
        ${options.map(([value, label]) => (
          `<option value="${h(value)}"${value === current ? " selected" : ""}>${h(label)}</option>`
        )).join("")}
      </select>`;
  }

  function issueQuickSelectHtml(issue, field, options) {
    return `
      <select data-issue-id="${h(issue.id)}" data-issue-field="${h(field)}" aria-label="${field === "status" ? "调整问题状态" : "调整问题优先级"}">
        ${optionsHtml(options, issue[field])}
      </select>`;
  }

  function optionsHtml(options, current) {
    return options.map(([value, label]) => (
      `<option value="${h(value)}"${value === current ? " selected" : ""}>${h(label)}</option>`
    )).join("");
  }

  function getSettingValue(row, field) {
    const input = row.querySelector(`[data-setting-field="${field}"]`);
    return input ? input.value.trim() : "";
  }

  function optionValueOrDefault(options, value, fallback) {
    return options.some(([option]) => option === value) ? value : fallback;
  }

  function labelFor(options, value) {
    const found = options.find(([option]) => option === value);
    return found ? found[1] : value;
  }

  function compactText(parts) {
    return parts.filter(Boolean).join("；");
  }

  function parseLocalDate(dateString) {
    const [year, month, day] = String(dateString).split("-").map(Number);
    return new Date(year, month - 1, day);
  }

  function toLocalDateString(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
  }

  function shiftDate(dateString, amount) {
    const date = parseLocalDate(dateString);
    date.setDate(date.getDate() + amount);
    return toLocalDateString(date);
  }

  function formatTime(date) {
    return date.toLocaleTimeString("zh-CN", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function formatDateTime(date) {
    return date.toLocaleString("zh-CN", {
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function csvCell(value) {
    const text = String(value ?? "");
    return `"${text.replace(/"/g, '""')}"`;
  }

  function downloadFile(filename, content, type) {
    const blob = new Blob([content], { type });
    downloadBlob(filename, blob);
  }

  function downloadBlob(filename, blob) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    els.toast.textContent = message;
    els.toast.classList.add("show");
    state.toastTimer = window.setTimeout(() => {
      els.toast.classList.remove("show");
    }, 2200);
  }

  function h(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[char]));
  }

  function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, "\\$&");
  }
})();
