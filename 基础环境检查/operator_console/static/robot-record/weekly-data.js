(function (root, factory) {
  "use strict";

  const problemData = typeof module === "object" && module.exports
    ? require("./problem-data")
    : root.RobotProblemData;
  const api = factory(problemData);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RobotWeeklyData = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function (problemData) {
  "use strict";

  function cleanText(value) {
    return String(value == null ? "" : value).replace(/\u00a0/g, " ").trim();
  }

  function addRobotCount(groups, key, robot, count) {
    const robots = groups.get(key) || new Map();
    robots.set(robot, (robots.get(robot) || 0) + count);
    groups.set(key, robots);
  }

  function robotCounts(groups, key) {
    return [...(groups.get(key)?.entries() || [])]
      .map(([robot, count]) => ({ robot, count }))
      .sort((a, b) => b.count - a.count || a.robot.localeCompare(b.robot, "zh-CN"));
  }

  function robotFrequency(robotDetails) {
    const groups = new Map();
    (Array.isArray(robotDetails) ? robotDetails : []).forEach((item) => {
      const current = groups.get(item.robot) || {
        robot: item.robot,
        count: 0,
        problems: []
      };
      current.count += Number(item.count) || 0;
      current.problems.push({
        issue: item.issue,
        count: Number(item.count) || 0,
        details: Array.isArray(item.details) ? item.details : []
      });
      groups.set(item.robot, current);
    });
    return [...groups.values()].sort((a, b) => b.count - a.count || a.robot.localeCompare(b.robot, "zh-CN"));
  }


  function aggregateProblemOccurrences(source, period, classificationCatalog) {
    const start = cleanText(period?.start);
    const end = cleanText(period?.end);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || start > end) {
      return null;
    }
    const occurrences = (Array.isArray(source) ? source : [])
      .map((item) => problemData.normalizeProblemOccurrence(item, { classificationCatalog }))
      .filter((item) => problemData.occurrenceOverlapsPeriod(item, { start, end }));
    const problemStats = new Map();
    const problemRobotStats = new Map();
    const systemStats = new Map();
    const systemProblemStats = new Map();
    const systemProblemRobotStats = new Map();
    const robotDetails = new Map();

    occurrences.forEach((occurrence) => {
      const count = Number(occurrence.count) || 0;
      if (!count) return;
      const issue = cleanText(occurrence.faultName) || "其他问题";
      const robot = cleanText(occurrence.robotId) || "未注明编号";
      const problem = problemStats.get(issue) || { issue, count: 0 };
      problem.count += count;
      problemStats.set(issue, problem);
      addRobotCount(problemRobotStats, issue, robot, count);

      const systemCategory = occurrence.systemCategory || "other";
      const system = systemStats.get(systemCategory) || {
        issue: problemData.systemLabel(systemCategory, classificationCatalog),
        systemCategory,
        count: 0
      };
      system.count += count;
      systemStats.set(systemCategory, system);
      const systemProblems = systemProblemStats.get(systemCategory) || new Map();
      const systemProblem = systemProblems.get(issue) || { issue, count: 0 };
      systemProblem.count += count;
      systemProblems.set(issue, systemProblem);
      systemProblemStats.set(systemCategory, systemProblems);
      const systemProblemRobots = systemProblemRobotStats.get(systemCategory) || new Map();
      addRobotCount(systemProblemRobots, issue, robot, count);
      systemProblemRobotStats.set(systemCategory, systemProblemRobots);

      const robotKey = `${robot}\u0000${issue}`;
      const detail = robotDetails.get(robotKey) || {
        robot,
        issue,
        count: 0,
        details: [],
        systemCategory,
        moduleCategory: occurrence.moduleCategory || "other",
        severity: occurrence.severity || "info"
      };
      detail.count += count;
      const rawIssue = cleanText(occurrence.rawIssue);
      if (rawIssue && !detail.details.includes(rawIssue)) detail.details.push(rawIssue);
      robotDetails.set(robotKey, detail);
    });

    return {
      weekStart: start,
      weekEnd: end,
      problemStats: [...problemStats.values()].map((problem) => ({
        ...problem,
        robots: robotCounts(problemRobotStats, problem.issue)
      })),
      systemStats: [...systemStats.values()].map((system) => ({
        ...system,
        problems: [...(systemProblemStats.get(system.systemCategory)?.values() || [])]
          .map((problem) => ({
            ...problem,
            robots: robotCounts(systemProblemRobotStats.get(system.systemCategory) || new Map(), problem.issue)
          }))
          .sort((a, b) => b.count - a.count || a.issue.localeCompare(b.issue, "zh-CN"))
      })),
      robotDetails: [...robotDetails.values()],
      sourceCount: new Set(occurrences.map((item) => item.sourceBatchId || item.id).filter(Boolean)).size
    };
  }

  function shiftDateString(value, offset) {
    const date = new Date(`${value}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10);
  }

  function previousDatePeriod(period) {
    const start = new Date(`${period.start}T00:00:00Z`);
    const end = new Date(`${period.end}T00:00:00Z`);
    const dayCount = Math.round((end - start) / 86400000) + 1;
    return {
      start: shiftDateString(period.start, -dayCount),
      end: shiftDateString(period.start, -1)
    };
  }

  function issueFrequencyLevel(count) {
    if (count >= 4) return "serious";
    if (count >= 2) return "warning";
    return "normal";
  }

  function robotFrequencyRisk(count) {
    if (count >= 5) return "highRisk";
    if (count >= 3) return "attention";
    return "minor";
  }

  function paretoItems(items, total) {
    let cumulativeCount = 0;
    return (Array.isArray(items) ? items : [])
      .slice()
      .sort((a, b) => b.count - a.count || a.issue.localeCompare(b.issue, "zh-CN"))
      .map((item) => {
        cumulativeCount += item.count;
        return {
          ...item,
          level: issueFrequencyLevel(item.count),
          cumulativePercent: total ? Number(((cumulativeCount / total) * 100).toFixed(1)) : 0
        };
      });
  }

  function buildProblemDashboard(source, period, robotTotal = 0, classificationCatalog) {
    const current = aggregateProblemOccurrences(source, period, classificationCatalog);
    if (!current) return null;
    const previousPeriod = previousDatePeriod(period);
    const previous = aggregateProblemOccurrences(source, previousPeriod, classificationCatalog);
    const twoPeriodsAgoPeriod = previousDatePeriod(previousPeriod);
    const twoPeriodsAgo = aggregateProblemOccurrences(source, twoPeriodsAgoPeriod, classificationCatalog);
    const issueTotal = current.problemStats.reduce((total, item) => total + item.count, 0);
    const previousTotal = previous.problemStats.reduce((total, item) => total + item.count, 0);
    const twoPeriodsAgoTotal = twoPeriodsAgo.problemStats.reduce((total, item) => total + item.count, 0);
    const issues = paretoItems(current.problemStats, issueTotal);
    const systems = paretoItems(current.systemStats, issueTotal);
    const robots = robotFrequency(current.robotDetails).map((robot) => ({
      ...robot,
      risk: robotFrequencyRisk(robot.count)
    }));
    const affectedRobotCount = robots.length;

    return {
      period: { start: period.start, end: period.end },
      previousPeriod,
      twoPeriodsAgoPeriod,
      current,
      previous,
      twoPeriodsAgo,
      robotTotal: Number(robotTotal) || 0,
      issueTotal,
      previousTotal,
      twoPeriodsAgoTotal,
      affectedRobotCount,
      affectedRatio: robotTotal ? Number(((affectedRobotCount / robotTotal) * 100).toFixed(1)) : 0,
      topIssue: issues[0] || null,
      topSystem: systems[0] || null,
      issues,
      systems,
      robots,
      changePercent: previousTotal
        ? Math.round(((issueTotal - previousTotal) / previousTotal) * 100)
        : null,
      twoPeriodsAgoChangePercent: twoPeriodsAgoTotal
        ? Math.round(((issueTotal - twoPeriodsAgoTotal) / twoPeriodsAgoTotal) * 100)
        : null
    };
  }

  function buildProblemRecordText(problemRecord) {
    if (!problemRecord) return "";
    const robots = robotFrequency(problemRecord.robotDetails);
    const issueTotal = problemRecord.problemStats.reduce((total, item) => total + item.count, 0);
    const lines = [
      `${problemRecord.weekStart} 至 ${problemRecord.weekEnd} 机器人问题记录`,
      "",
      `所选范围共记录 ${issueTotal} 次问题，涉及 ${robots.length} 台机器人、${problemRecord.problemStats.length} 类问题。`,
      "",
      "一、问题频次",
      ...problemRecord.problemStats
        .slice()
        .sort((a, b) => b.count - a.count || a.issue.localeCompare(b.issue, "zh-CN"))
        .map((item) => `- ${item.issue}：${item.count} 次`),
      "",
      "二、系统来源",
      ...(problemRecord.systemStats || [])
        .slice()
        .sort((a, b) => b.count - a.count || a.issue.localeCompare(b.issue, "zh-CN"))
        .map((item) => `- ${item.issue}：${item.count} 次`),
      "",
      "三、机器人问题频次",
      ...robots.map((item) => `- ${item.robot}：${item.count} 次`),
      "",
      "四、机器人明细",
      ...robots.map((item) => {
        const problems = item.problems.map((problem) => (
          `${problem.issue} ${problem.count} 次${problem.details.length ? `（${problem.details.join("；")}）` : ""}`
        )).join("；");
        return `- ${item.robot}：${problems}`;
      })
    ];
    return lines.join("\n");
  }

  return {
    aggregateProblemOccurrences,
    buildProblemDashboard,
    buildProblemRecordText
  };
}));
