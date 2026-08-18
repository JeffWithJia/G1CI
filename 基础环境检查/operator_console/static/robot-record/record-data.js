(function (root, factory) {
  "use strict";

  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RobotRecordData = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const datePattern = /^\d{4}-\d{2}-\d{2}$/;

  function resolveRecordAtDate(records, date, robotId) {
    if (!records || typeof records !== "object" || Array.isArray(records)) return null;
    if (!datePattern.test(String(date || "")) || !robotId) return null;

    const sourceDate = Object.keys(records)
      .filter((candidateDate) => (
        datePattern.test(candidateDate) &&
        candidateDate <= date &&
        records[candidateDate] &&
        typeof records[candidateDate] === "object" &&
        !Array.isArray(records[candidateDate]) &&
        Object.prototype.hasOwnProperty.call(records[candidateDate], robotId) &&
        records[candidateDate][robotId] &&
        typeof records[candidateDate][robotId] === "object" &&
        !Array.isArray(records[candidateDate][robotId])
      ))
      .sort()
      .at(-1);

    if (!sourceDate) return null;
    return {
      sourceDate,
      record: records[sourceDate][robotId]
    };
  }

  function countRepairStatuses(records) {
    return (Array.isArray(records) ? records : []).reduce((counts, record) => {
      if (record && Object.prototype.hasOwnProperty.call(counts, record.repairStatus)) {
        counts[record.repairStatus] += 1;
      }
      return counts;
    }, {
      repairing: 0,
      afterSales: 0,
      awayRepair: 0
    });
  }

  function resolveTrendPeriod(dateString, range, customStart = "", customEnd = "") {
    if (range === "custom") {
      const start = parseDateString(customStart);
      const end = parseDateString(customEnd);
      if (!start || !end || start > end) return null;
      return { start: formatDateString(start), end: formatDateString(end) };
    }

    const date = parseDateString(dateString);
    if (!date) return null;
    if (range === "month" || range === "previousMonth") {
      const offset = range === "previousMonth" ? -1 : 0;
      const year = date.getUTCFullYear();
      const month = date.getUTCMonth() + offset;
      return {
        start: formatDateString(new Date(Date.UTC(year, month, 1))),
        end: formatDateString(new Date(Date.UTC(year, month + 1, 0)))
      };
    }

    const day = date.getUTCDay();
    const mondayOffset = day === 0 ? -6 : 1 - day;
    const previousOffset = range === "previousWeek" ? -7 : 0;
    const start = new Date(date);
    start.setUTCDate(date.getUTCDate() + mondayOffset + previousOffset);
    const end = new Date(start);
    end.setUTCDate(start.getUTCDate() + 6);
    return { start: formatDateString(start), end: formatDateString(end) };
  }

  function parseDateString(value) {
    const text = String(value || "");
    if (!datePattern.test(text)) return null;
    const [year, month, day] = text.split("-").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return formatDateString(date) === text ? date : null;
  }

  function formatDateString(date) {
    return date.toISOString().slice(0, 10);
  }

  return { resolveRecordAtDate, countRepairStatuses, resolveTrendPeriod };
}));
