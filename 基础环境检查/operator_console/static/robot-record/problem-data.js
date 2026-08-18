(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RobotProblemData = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const TAXONOMY_VERSION = "robot-fault-v1";
  const SYSTEM_CATEGORIES = [
    ["perception", "感知系统"],
    ["motion", "运动系统"],
    ["dexterous_hand", "灵巧手系统"],
    ["data", "数据系统"],
    ["network", "通信网络"],
    ["software", "软件环境"],
    ["other", "其他"]
  ];
  const MODULE_CATEGORIES = {
    perception: [
      ["head_camera", "Head Camera"],
      ["wrist_camera", "Wrist Camera"],
      ["depth_camera", "Depth Camera"],
      ["sensor", "Sensor"]
    ],
    motion: [
      ["locomotion", "Locomotion"],
      ["balance", "Balance"],
      ["controller", "Controller"]
    ],
    dexterous_hand: [
      ["left_hand", "Left Hand"],
      ["right_hand", "Right Hand"],
      ["gripper", "Gripper"]
    ],
    data: [
      ["recording", "Recording"],
      ["metadata", "Metadata"],
      ["time_sync", "Time Sync"]
    ],
    network: [
      ["network", "Network"],
      ["dds", "DDS"],
      ["server", "Server"]
    ],
    software: [
      ["ros", "ROS"],
      ["service", "Service"],
      ["driver", "Driver"]
    ],
    other: [["other", "其他"]]
  };
  const SEVERITIES = [
    ["info", "一般"],
    ["warning", "警告"],
    ["critical", "严重"]
  ];
  const PRIORITIES = [
    ["low", "低"],
    ["medium", "中"],
    ["high", "高"],
    ["urgent", "紧急"]
  ];
  const DEFAULT_FAULTS = {
    "perception/head_camera": [
      { name: "头部摄像头数据缺失", keywords: ["头部摄像头没数据", "头部相机无数据"] }
    ],
    "perception/sensor": [
      { name: "摄像头帧率不足", keywords: ["帧率不足", "掉帧"] }
    ],
    "motion/balance": [
      { name: "突然倒地", keywords: ["倒地", "摔倒", "后倒"] }
    ],
    "motion/locomotion": [
      { name: "腿部外八", keywords: ["腿部外八", "左腿外八", "右腿外八"] }
    ],
    "dexterous_hand/gripper": [
      { name: "夹爪服务掉线", keywords: ["夹爪服务掉线", "夹爪服务掉落"] }
    ],
    "data/recording": [
      { name: "录制异常", keywords: ["录制卡顿", "录制失败", "bag异常"] }
    ],
    "network/network": [
      { name: "机器人掉线", keywords: ["机器人掉线", "网络断连"] }
    ],
    "software/service": [
      { name: "服务启动失败", keywords: ["服务启动失败", "服务异常"] }
    ]
  };
  const SYSTEM_VALUES = new Set(SYSTEM_CATEGORIES.map(([value]) => value));
  const SEVERITY_VALUES = new Set(SEVERITIES.map(([value]) => value));
  const PRIORITY_VALUES = new Set(PRIORITIES.map(([value]) => value));
  const NON_FAULT_CONTEXT_PATTERN = /测试|版本|升级|部署|发布|售后|返厂|寄修|送修|催促|沟通|对接|进度/;
  const EXPLICIT_FAULT_PATTERN = /异常|故障|失败|无法|不能|掉线|断连|倒地|摔倒|后倒|缺失|没数据|无数据|掉帧|帧率不足|卡顿|失控|停止|不可用|报错|外八|损坏|掉落|异响|发热|不工作/;

  function cleanText(value) {
    return String(value ?? "").replace(/\r/g, "").trim();
  }

  function labelFor(options, value, fallback = "其他") {
    return options.find(([key]) => key === value)?.[1] || fallback;
  }

  function normalizedFaultRules(value, fallback) {
    const source = Array.isArray(value) ? value : fallback;
    const seen = new Set();
    return (Array.isArray(source) ? source : []).slice(0, 200).map((item) => {
      const name = cleanText(item?.name).slice(0, 80);
      const keywords = (Array.isArray(item?.keywords)
        ? item.keywords
        : cleanText(item?.keywords).split(/[、,，;；\n]/))
        .map((keyword) => cleanText(keyword).slice(0, 80))
        .filter(Boolean)
        .filter((keyword, index, values) => values.indexOf(keyword) === index)
        .slice(0, 30);
      return { name, keywords };
    }).filter((item) => item.name && !seen.has(item.name) && seen.add(item.name));
  }

  function normalizeClassificationCatalog(value = {}) {
    const sourceSystems = Array.isArray(value?.systems) ? value.systems : [];
    return {
      version: 1,
      systems: SYSTEM_CATEGORIES.map(([systemId, defaultLabel]) => {
        const sourceSystem = sourceSystems.find((item) => item?.id === systemId);
        const sourceModules = Array.isArray(sourceSystem?.modules) ? sourceSystem.modules : [];
        return {
          id: systemId,
          label: cleanText(sourceSystem?.label).slice(0, 60) || defaultLabel,
          modules: (MODULE_CATEGORIES[systemId] || MODULE_CATEGORIES.other).map(([moduleId, moduleDefaultLabel]) => {
            const sourceModule = sourceModules.find((item) => item?.id === moduleId);
            return {
              id: moduleId,
              label: cleanText(sourceModule?.label).slice(0, 60) || moduleDefaultLabel,
              faults: normalizedFaultRules(sourceModule?.faults, DEFAULT_FAULTS[`${systemId}/${moduleId}`] || [])
            };
          })
        };
      })
    };
  }

  function defaultClassificationCatalog() {
    return normalizeClassificationCatalog();
  }

  function catalogSystem(catalog, systemCategory) {
    return catalog?.systems?.find((item) => item.id === systemCategory);
  }

  function systemLabel(value, catalog) {
    return catalogSystem(catalog, value)?.label || labelFor(SYSTEM_CATEGORIES, value);
  }

  function moduleOptions(systemCategory, catalog) {
    const modules = catalogSystem(catalog, systemCategory)?.modules;
    return modules?.length
      ? modules.map((item) => [item.id, item.label])
      : MODULE_CATEGORIES[systemCategory] || MODULE_CATEGORIES.other;
  }

  function moduleLabel(systemCategory, value, catalog) {
    return labelFor(moduleOptions(systemCategory, catalog), value);
  }

  function severityLabel(value) {
    return labelFor(SEVERITIES, value, "一般");
  }

  function stableHash(value) {
    let hash = 2166136261;
    const input = String(value || "");
    for (let index = 0; index < input.length; index += 1) {
      hash ^= input.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(36).toUpperCase();
  }

  function canonicalFaultName(input) {
    const name = cleanText(input);
    if (/夹爪.*服务.*(?:掉落|掉线)/i.test(name)) return "夹爪服务掉线";
    if (/(?:腿部|左腿|右腿).*外八|^外八/.test(name)) return "腿部外八";
    if (/^(?:机器人)?掉线$/i.test(name)) return "机器人掉线";
    if (/^pico.*(?:看不到|无法查看).*任务$/i.test(name)) return "Pico看不到任务";
    return name;
  }

  function inferSystemAndModule(input) {
    const text = cleanText(input).toLowerCase();
    if (/左手|左侧手|left\s*hand/.test(text)) return { systemCategory: "dexterous_hand", moduleCategory: "left_hand" };
    if (/右手|右侧手|right\s*hand/.test(text)) return { systemCategory: "dexterous_hand", moduleCategory: "right_hand" };
    if (/夹爪|手指|拇指|抓取|灵巧手|brainco|\bdex\b|gripper/.test(text)) {
      return { systemCategory: "dexterous_hand", moduleCategory: "gripper" };
    }
    if (/腕部.*(?:camera|摄像头)|wrist\s*camera/.test(text)) {
      return { systemCategory: "perception", moduleCategory: "wrist_camera" };
    }
    if (/头部.*(?:camera|摄像头|相机)|head\s*camera/.test(text)) {
      return { systemCategory: "perception", moduleCategory: "head_camera" };
    }
    if (/深度|zed|realsense|depth\s*camera/.test(text)) {
      return { systemCategory: "perception", moduleCategory: "depth_camera" };
    }
    if (/camera|摄像头|相机|图像|画面|帧率|掉帧|传感器|sensor/.test(text)) {
      return { systemCategory: "perception", moduleCategory: "sensor" };
    }
    if (/倒地|摔倒|后倒|失衡|balance/.test(text)) return { systemCategory: "motion", moduleCategory: "balance" };
    if (/行走|走路|步态|外八|腿|locomotion/.test(text)) return { systemCategory: "motion", moduleCategory: "locomotion" };
    if (/运控|运动|controller|控制器|手臂.*(?:下摆|异常)/.test(text)) {
      return { systemCategory: "motion", moduleCategory: "controller" };
    }
    if (/录制|record|\bbag\b|采集数据/.test(text)) return { systemCategory: "data", moduleCategory: "recording" };
    if (/时间同步|timestamp|时间差|时钟/.test(text)) return { systemCategory: "data", moduleCategory: "time_sync" };
    if (/元数据|metadata|上传|数据标记/.test(text)) return { systemCategory: "data", moduleCategory: "metadata" };
    if (/\bdds\b|\btopic\b/.test(text)) return { systemCategory: "network", moduleCategory: "dds" };
    if (/服务器|server/.test(text)) return { systemCategory: "network", moduleCategory: "server" };
    if (/wifi|wi-fi|网络|连接|ping|机器人掉线|通信|断连/.test(text)) {
      return { systemCategory: "network", moduleCategory: "network" };
    }
    if (/\bros\b/.test(text)) return { systemCategory: "software", moduleCategory: "ros" };
    if (/驱动|driver/.test(text)) return { systemCategory: "software", moduleCategory: "driver" };
    if (/服务|启动失败|docker|systemd|\bnode\b|teleop|进程/.test(text)) {
      return { systemCategory: "software", moduleCategory: "service" };
    }
    return { systemCategory: "other", moduleCategory: "other" };
  }

  function inferSeverity(input) {
    const text = cleanText(input).toLowerCase();
    if (/倒地|摔倒|无法运动|不能运动|失控|核心服务.*(?:挂|停止|不可用)|急停失效/.test(text)) return "critical";
    if (/掉帧|帧率|数据缺失|没数据|无数据|无法操控|连接失败|手.*异常|夹爪|录制.*(?:异常|卡顿|失败)|掉线|断连|摄像头|相机|服务异常|启动失败/.test(text)) {
      return "warning";
    }
    return "info";
  }

  function priorityForSeverity(severity, isP0 = false) {
    if (isP0 || severity === "critical") return "urgent";
    if (severity === "warning") return "medium";
    return "low";
  }

  function catalogFaultMatch(input, catalog) {
    if (!catalog?.systems) return null;
    const text = cleanText(input).toLowerCase();
    let match = null;
    catalog.systems.forEach((system) => system.modules?.forEach((module) => module.faults?.forEach((fault) => {
      const terms = [fault.name, ...(fault.keywords || [])].map((term) => cleanText(term)).filter(Boolean);
      const matchedTerm = terms.filter((term) => text.includes(term.toLowerCase())).sort((a, b) => b.length - a.length)[0];
      if (!matchedTerm || (match && matchedTerm.length <= match.term.length)) return;
      match = { systemCategory: system.id, moduleCategory: module.id, faultName: fault.name, term: matchedTerm };
    })));
    return match;
  }

  function classifyFault(input, catalog) {
    const sourceFaultName = cleanText(input?.faultName || input?.fault_name || input?.rawIssue || input?.raw_issue || input);
    const rawIssue = cleanText(input?.rawIssue || input?.raw_issue || "");
    const combined = `${sourceFaultName} ${rawIssue}`.trim();
    const catalogMatch = catalogFaultMatch(combined, catalog);
    const faultName = catalogMatch?.faultName || canonicalFaultName(sourceFaultName);
    const system = catalogMatch || inferSystemAndModule(combined);
    const severity = inferSeverity(combined);
    return {
      faultName,
      ...system,
      severity,
      priority: priorityForSeverity(severity, Boolean(input?.isP0 || input?.is_p0))
    };
  }

  function isLedgerEligibleReportOccurrence(value = {}) {
    const rawIssue = cleanText(value.rawIssue || value.raw_issue);
    const faultName = cleanText(value.faultName || value.fault_name || value.name);
    const combined = `${rawIssue} ${faultName}`.trim();
    if (!NON_FAULT_CONTEXT_PATTERN.test(combined)) return true;
    const robotId = cleanText(value.robotId || value.robot_id || value.robot);
    const hasSpecificRobot = Boolean(robotId && !/^(?:未注明编号|未知|无|unknown)$/i.test(robotId));
    return hasSpecificRobot && EXPLICIT_FAULT_PATTERN.test(combined);
  }

  function validModuleForSystem(systemCategory, moduleCategory) {
    return moduleCategory === "other" || moduleOptions(systemCategory).some(([value]) => value === moduleCategory);
  }

  function positiveCount(value) {
    const count = Number(value);
    return Number.isFinite(count) && count > 0 ? Math.round(count) : 1;
  }

  function normalizeProblemOccurrence(value = {}, defaults = {}) {
    const rawIssue = cleanText(value.rawIssue || value.raw_issue || value.detail || value.details?.join("；") || defaults.rawIssue);
    const suppliedFaultName = cleanText(value.faultName || value.fault_name || value.name || value.issue || defaults.faultName || rawIssue);
    const combined = `${suppliedFaultName} ${rawIssue}`.trim();
    const catalogMatch = catalogFaultMatch(combined, defaults.classificationCatalog);
    const inferred = classifyFault(
      { faultName: suppliedFaultName, rawIssue, isP0: value.isP0 || value.is_p0 },
      defaults.classificationCatalog
    );
    let systemCategory = cleanText(value.systemCategory || value.system_category || defaults.systemCategory);
    if (catalogMatch || !SYSTEM_VALUES.has(systemCategory)) systemCategory = inferred.systemCategory;
    let moduleCategory = cleanText(value.moduleCategory || value.module_category || defaults.moduleCategory);
    if (catalogMatch) moduleCategory = inferred.moduleCategory;
    else if (!validModuleForSystem(systemCategory, moduleCategory)) {
      moduleCategory = inferred.systemCategory === systemCategory ? inferred.moduleCategory : "other";
    }
    let severity = cleanText(value.severity || defaults.severity);
    if (!SEVERITY_VALUES.has(severity)) severity = inferred.severity;
    const isP0 = Boolean(value.isP0 || value.is_p0 || value.priority === "P0" || defaults.isP0);
    let priority = cleanText(value.priority || defaults.priority);
    if (priority === "critical") priority = "urgent";
    if (!PRIORITY_VALUES.has(priority)) priority = priorityForSeverity(severity, isP0);
    const occurredAt = cleanText(value.occurredAt || value.occurred_at || value.date || defaults.occurredAt);
    const periodStart = cleanText(value.periodStart || value.period_start || defaults.periodStart || occurredAt.slice(0, 10));
    const periodEnd = cleanText(value.periodEnd || value.period_end || defaults.periodEnd || periodStart);
    const datePrecision = cleanText(value.datePrecision || value.date_precision || defaults.datePrecision) || (
      /^\d{4}-\d{2}-\d{2}T/.test(occurredAt) ? "datetime" : /^\d{4}-\d{2}-\d{2}$/.test(occurredAt) ? "day" : "period"
    );
    const confidenceValue = Number(value.classificationConfidence ?? value.classification_confidence ?? defaults.classificationConfidence);
    const classificationConfidence = Number.isFinite(confidenceValue)
      ? Math.max(0, Math.min(1, confidenceValue))
      : null;
    const robotId = cleanText(value.robotId || value.robot_id || value.robot || defaults.robotId) || "未注明编号";
    const sourceBatchId = cleanText(value.sourceBatchId || value.source_batch_id || defaults.sourceBatchId);
    const sourceType = cleanText(value.sourceType || value.source_type || defaults.sourceType) || "manual";
    const sourceRef = cleanText(value.sourceRef || value.source_ref || defaults.sourceRef);
    const identity = [sourceBatchId, sourceRef, robotId, occurredAt, suppliedFaultName, rawIssue].join("|");

    return {
      id: cleanText(value.id || defaults.id) || `OCC-${stableHash(identity)}`,
      sourceBatchId,
      sourceType,
      sourceRef,
      periodStart,
      periodEnd,
      occurredAt,
      datePrecision: ["datetime", "day", "week", "period"].includes(datePrecision) ? datePrecision : "period",
      robotId,
      rawIssue: rawIssue || suppliedFaultName || "未填写现场现象",
      faultName: inferred.faultName || "其他问题",
      systemCategory,
      moduleCategory,
      severity,
      priority,
      isP0,
      count: positiveCount(value.count ?? defaults.count),
      status: cleanText(value.status || defaults.status) || (sourceType === "codex_weekly_report" ? "" : "open"),
      taxonomyVersion: cleanText(value.taxonomyVersion || value.taxonomy_version || defaults.taxonomyVersion) || TAXONOMY_VERSION,
      classificationSource: cleanText(value.classificationSource || value.classification_source || defaults.classificationSource) || "rules",
      classificationConfidence,
      createdAt: cleanText(value.createdAt || value.created_at || defaults.createdAt),
      updatedAt: cleanText(value.updatedAt || value.updated_at || defaults.updatedAt)
    };
  }

  function utcDate(year, month, day) {
    const date = new Date(Date.UTC(year, month - 1, day));
    if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
    return date;
  }

  function dateString(date) {
    return date.toISOString().slice(0, 10);
  }

  function weekPeriodForDate(date) {
    if (!(date instanceof Date) || Number.isNaN(date.getTime())) return null;
    const monday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
    const day = monday.getUTCDay() || 7;
    monday.setUTCDate(monday.getUTCDate() - day + 1);
    const end = new Date(monday);
    end.setUTCDate(end.getUTCDate() + 6);
    return { start: dateString(monday), end: dateString(end) };
  }

  function isoWeekPeriod(year, week) {
    if (!Number.isInteger(year) || !Number.isInteger(week) || week < 1 || week > 53) return null;
    const januaryFourth = utcDate(year, 1, 4);
    const monday = new Date(januaryFourth);
    monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() || 7) + 1 + (week - 1) * 7);
    const thursday = new Date(monday);
    thursday.setUTCDate(thursday.getUTCDate() + 3);
    if (thursday.getUTCFullYear() !== year) return null;
    const end = new Date(monday);
    end.setUTCDate(end.getUTCDate() + 6);
    return { start: dateString(monday), end: dateString(end) };
  }

  function canonicalReportWeek(startDate, endDate = startDate) {
    if (!startDate || !endDate || startDate > endDate) return null;
    const week = weekPeriodForDate(startDate);
    const end = dateString(endDate);
    return week && end >= week.start && end <= week.end ? week : null;
  }

  function resolveReportPeriod(value, referenceDate = new Date()) {
    const input = cleanText(value).replace(/[—–~～到]/g, "至");
    const isoWeek = input.match(/^(\d{4})-W(\d{2})$/i);
    if (isoWeek) return isoWeekPeriod(Number(isoWeek[1]), Number(isoWeek[2]));
    const full = input.match(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*(?:至|\s+-\s+)\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
    if (full) {
      const startDate = utcDate(Number(full[1]), Number(full[2]), Number(full[3]));
      const endDate = utcDate(Number(full[4]), Number(full[5]), Number(full[6]));
      return canonicalReportWeek(startDate, endDate);
    }
    const short = input.match(/(\d{1,2})[./](\d{1,2})\s*(?:至|-)\s*(\d{1,2})[./](\d{1,2})/);
    if (short) {
      const reference = referenceDate instanceof Date ? referenceDate : new Date(referenceDate);
      const safeReference = Number.isNaN(reference.getTime()) ? new Date() : reference;
      const startYear = safeReference.getFullYear();
      const startDate = utcDate(startYear, Number(short[1]), Number(short[2]));
      let endYear = startYear;
      if (Number(short[3]) < Number(short[1])) endYear += 1;
      const endDate = utcDate(endYear, Number(short[3]), Number(short[4]));
      return canonicalReportWeek(startDate, endDate);
    }
    const single = input.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
    if (single) {
      const date = utcDate(Number(single[1]), Number(single[2]), Number(single[3]));
      return canonicalReportWeek(date);
    }
    return null;
  }

  function reportPeriodLabel(value, referenceDate = new Date()) {
    const period = resolveReportPeriod(value, referenceDate);
    return period ? `${period.start} 至 ${period.end}` : "";
  }

  function occurrenceOverlapsPeriod(occurrence, period) {
    const start = cleanText(period?.start);
    const end = cleanText(period?.end);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) return false;
    const exactDate = cleanText(occurrence?.occurredAt).match(/^(\d{4}-\d{2}-\d{2})/)?.[1];
    if (exactDate && ["day", "datetime"].includes(occurrence?.datePrecision)) {
      return exactDate >= start && exactDate <= end;
    }
    const sourceStart = cleanText(occurrence?.periodStart || exactDate);
    const sourceEnd = cleanText(occurrence?.periodEnd || sourceStart);
    return Boolean(sourceStart && sourceEnd && sourceStart <= end && sourceEnd >= start);
  }

  function legacyWeeklyImportOccurrences(weeklyImport) {
    const periodStart = cleanText(weeklyImport?.weekStart);
    const periodEnd = cleanText(weeklyImport?.weekEnd || periodStart);
    if (!periodStart || !periodEnd) return [];
    const sourceBatchId = `legacy-weekly:${cleanText(weeklyImport.id) || periodStart}`;
    const importedAt = cleanText(weeklyImport.importedAt);
    const occurrences = [];
    const detailTotals = new Map();
    (Array.isArray(weeklyImport.robotDetails) ? weeklyImport.robotDetails : []).forEach((detail, index) => {
      const faultName = canonicalFaultName(detail?.issue);
      const robotId = cleanText(detail?.robot) || "未注明编号";
      if (!faultName) return;
      const count = positiveCount(detail?.count);
      detailTotals.set(faultName, (detailTotals.get(faultName) || 0) + count);
      const rawIssue = (Array.isArray(detail?.details) ? detail.details.map(cleanText).filter(Boolean).join("；") : "") || faultName;
      occurrences.push(normalizeProblemOccurrence({
        sourceRef: `detail:${index + 1}`,
        robotId,
        rawIssue,
        faultName,
        count
      }, {
        sourceBatchId,
        sourceType: "legacy_weekly_import",
        periodStart,
        periodEnd,
        occurredAt: periodStart,
        datePrecision: "week",
        classificationSource: "rules",
        createdAt: importedAt,
        updatedAt: importedAt
      }));
    });
    (Array.isArray(weeklyImport.problemStats) ? weeklyImport.problemStats : []).forEach((summary, index) => {
      const faultName = canonicalFaultName(summary?.issue);
      if (!faultName) return;
      const missingCount = Math.max(0, positiveCount(summary?.count) - (detailTotals.get(faultName) || 0));
      if (!missingCount) return;
      occurrences.push(normalizeProblemOccurrence({
        sourceRef: `summary:${index + 1}`,
        robotId: "未注明编号",
        rawIssue: faultName,
        faultName,
        count: missingCount
      }, {
        sourceBatchId,
        sourceType: "legacy_weekly_import",
        periodStart,
        periodEnd,
        occurredAt: periodStart,
        datePrecision: "week",
        classificationSource: "rules",
        createdAt: importedAt,
        updatedAt: importedAt
      }));
    });
    return occurrences;
  }

  function oldIssueSystemCategory(category) {
    return ({
      control: "motion",
      collection: "data",
      camera: "perception",
      manipulator: "dexterous_hand",
      network: "network",
      software: "software"
    })[category] || "";
  }

  function manualIssueOccurrences(issues, robots, classificationCatalog) {
    const robotList = Array.isArray(robots) ? robots : [];
    return (Array.isArray(issues) ? issues : []).map((issue) => {
      const robot = robotList.find((candidate) => candidate.id === issue?.robotId);
      const rawIssue = cleanText(issue?.rawIssue || issue?.symptom);
      const inferred = classifyFault(
        { faultName: issue?.faultName || rawIssue, rawIssue, isP0: issue?.isP0 },
        classificationCatalog
      );
      const systemCategory = cleanText(issue?.systemCategory) || oldIssueSystemCategory(issue?.category) || inferred.systemCategory;
      const occurredAt = cleanText(issue?.occurredAt || issue?.createdAt);
      const occurredDate = occurredAt.slice(0, 10);
      return normalizeProblemOccurrence({
        id: `OCC-MANUAL-${cleanText(issue?.id) || stableHash(`${robot?.code}|${occurredAt}|${rawIssue}`)}`,
        sourceRef: cleanText(issue?.id),
        robotId: cleanText(issue?.robotCode || robot?.code || issue?.robotId),
        rawIssue,
        faultName: issue?.faultName || inferred.faultName,
        systemCategory,
        moduleCategory: issue?.moduleCategory,
        severity: issue?.faultSeverity,
        priority: issue?.priority || issue?.severity,
        isP0: issue?.isP0,
        count: 1,
        status: issue?.status
      }, {
        sourceBatchId: "manual-issues",
        sourceType: "manual_issue",
        periodStart: occurredDate,
        periodEnd: occurredDate,
        occurredAt,
        classificationSource: cleanText(issue?.classificationSource) || "rules",
        createdAt: issue?.createdAt,
        updatedAt: issue?.updatedAt,
        classificationCatalog
      });
    });
  }

  function canonicalReportOccurrences(occurrences) {
    const other = [];
    const batches = new Map();
    occurrences.forEach((item, index) => {
      if (item.sourceType !== "codex_weekly_report") {
        other.push(item);
        return;
      }
      const week = resolveReportPeriod(`${item.periodStart} 至 ${item.periodEnd}`);
      if (!week) {
        other.push(item);
        return;
      }
      const batchId = item.sourceBatchId || `legacy-report-batch:${index}`;
      const batchKey = `${week.start}|${batchId}`;
      const timestamp = Date.parse(item.updatedAt || item.createdAt || "") || 0;
      const batch = batches.get(batchKey) || { batchId, week, items: [], timestamp: 0, index };
      batch.items.push(item);
      batch.timestamp = Math.max(batch.timestamp, timestamp);
      batch.index = Math.max(batch.index, index);
      batches.set(batchKey, batch);
    });
    const latestByWeek = new Map();
    batches.forEach((batch) => {
      const current = latestByWeek.get(batch.week.start);
      if (!current || batch.timestamp > current.timestamp || (
        batch.timestamp === current.timestamp && batch.index > current.index
      )) {
        latestByWeek.set(batch.week.start, batch);
      }
    });
    const reports = [...latestByWeek.values()].flatMap((batch) => batch.items.map((item) => ({
      ...item,
      sourceBatchId: `codex-weekly-report:${batch.week.start}:${batch.week.end}`,
      periodStart: batch.week.start,
      periodEnd: batch.week.end
    })));
    return [...other, ...reports];
  }

  function rebuildProblemOccurrences(store = {}) {
    const existing = (Array.isArray(store.problemOccurrences) ? store.problemOccurrences : [])
      .map((item) => normalizeProblemOccurrence(item, { classificationCatalog: store.classificationCatalog }));
    const retained = canonicalReportOccurrences(
      existing.filter((item) => item.sourceType !== "manual_issue")
    );
    const reportPeriods = new Set(retained
      .filter((item) => item.sourceType === "codex_weekly_report")
      .map((item) => `${item.periodStart}|${item.periodEnd}`));
    const legacy = retained.some((item) => item.sourceType === "legacy_weekly_import")
      ? []
      : (Array.isArray(store.weeklyImports) ? store.weeklyImports : [])
        .filter((item) => !reportPeriods.has(`${cleanText(item?.weekStart)}|${cleanText(item?.weekEnd)}`))
        .flatMap(legacyWeeklyImportOccurrences);
    const manual = manualIssueOccurrences(store.issues, store.robots, store.classificationCatalog);
    return [...retained, ...legacy, ...manual];
  }

  function migrateRobotRecordStore(store = {}) {
    const next = {
      ...store,
      version: 6,
      robots: Array.isArray(store.robots) ? store.robots : [],
      records: store.records && typeof store.records === "object" && !Array.isArray(store.records) ? store.records : {},
      issues: Array.isArray(store.issues) ? store.issues : []
    };
    delete next.weeklyImports;
    next.classificationCatalog = normalizeClassificationCatalog(store.classificationCatalog);
    next.problemOccurrences = rebuildProblemOccurrences({ ...next, weeklyImports: store.weeklyImports });
    return next;
  }

  function syncReportOccurrences(store, reportOccurrences, options = {}) {
    const migrated = migrateRobotRecordStore(store);
    const requestedStart = cleanText(options.periodStart);
    const requestedEnd = cleanText(options.periodEnd || requestedStart);
    const reportWeek = resolveReportPeriod(`${requestedStart} 至 ${requestedEnd}`);
    if (!reportWeek) throw new Error("周报周期必须位于同一个自然周内。");
    const periodStart = reportWeek.start;
    const periodEnd = reportWeek.end;
    const sourceBatchId = cleanText(options.sourceBatchId) || `codex-weekly-report:${periodStart}:${periodEnd}`;
    const overlapsReportWeek = (item) => {
      const start = cleanText(item?.periodStart);
      const end = cleanText(item?.periodEnd || start);
      return Boolean(start && end && start <= periodEnd && end >= periodStart);
    };
    const replacesReportItem = (item) => (
      item.sourceType === "codex_weekly_report" && overlapsReportWeek(item)
    );
    const existingBatch = migrated.problemOccurrences.filter(replacesReportItem);
    const remaining = migrated.problemOccurrences.filter((item) => (
      !replacesReportItem(item) && !(item.sourceType === "legacy_weekly_import" && overlapsReportWeek(item))
    ));
    const now = cleanText(options.syncedAt) || new Date().toISOString();
    const sourceOccurrences = Array.isArray(reportOccurrences) ? reportOccurrences : [];
    const eligibleOccurrences = sourceOccurrences.filter(isLedgerEligibleReportOccurrence);
    const incoming = eligibleOccurrences.map((item, index) => {
      const occurredAt = cleanText(item?.occurredAt || item?.occurred_at || item?.date);
      const suppliedPrecision = cleanText(item?.datePrecision || item?.date_precision);
      const datePrecision = suppliedPrecision || (
        /^\d{4}-\d{2}-\d{2}T/.test(occurredAt)
          ? "datetime"
          : /^\d{4}-\d{2}-\d{2}$/.test(occurredAt)
            ? "day"
            : periodStart === periodEnd ? "day" : "period"
      );
      return normalizeProblemOccurrence({
        ...item,
        id: "",
        sourceBatchId: "",
        sourceType: "",
        sourceRef: "",
        periodStart: "",
        periodEnd: ""
      }, {
        id: `OCC-${stableHash(`${sourceBatchId}|${index + 1}|${item?.robotId}|${item?.occurredAt}|${item?.rawIssue}`)}`,
        sourceBatchId,
        sourceType: "codex_weekly_report",
        sourceRef: `occurrence:${index + 1}`,
        periodStart,
        periodEnd,
        occurredAt: periodStart,
        datePrecision,
        taxonomyVersion: TAXONOMY_VERSION,
        classificationSource: "codex",
        createdAt: now,
        updatedAt: now,
        classificationCatalog: migrated.classificationCatalog
      });
    });
    migrated.problemOccurrences = [...remaining, ...incoming];
    return {
      store: migrated,
      summary: {
        sourceBatchId,
        occurrenceCount: incoming.length,
        issueCount: incoming.reduce((total, item) => total + item.count, 0),
        replacedCount: existingBatch.length,
        skippedCount: sourceOccurrences.length - eligibleOccurrences.length,
        periodStart,
        periodEnd
      }
    };
  }

  return {
    MODULE_CATEGORIES,
    PRIORITIES,
    SEVERITIES,
    SYSTEM_CATEGORIES,
    canonicalFaultName,
    classifyFault,
    defaultClassificationCatalog,
    isLedgerEligibleReportOccurrence,
    manualIssueOccurrences,
    migrateRobotRecordStore,
    moduleLabel,
    moduleOptions,
    normalizeClassificationCatalog,
    normalizeProblemOccurrence,
    occurrenceOverlapsPeriod,
    rebuildProblemOccurrences,
    reportPeriodLabel,
    resolveReportPeriod,
    severityLabel,
    stableHash,
    syncReportOccurrences,
    systemLabel,
    validModuleForSystem
  };
}));
