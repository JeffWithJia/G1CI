(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RobotProjectData = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const PORTFOLIO_VERSION = 7;
  const DEFAULT_PROJECT_ID = "project-a";
  const DEFAULT_PROJECT_NAME = "湾谷机器人状态看板";

  function isPortfolio(value) {
    return Boolean(value && value.version === PORTFOLIO_VERSION && Array.isArray(value.projects));
  }

  function wrapLegacyStore(store, now = new Date().toISOString()) {
    return {
      version: PORTFOLIO_VERSION,
      activeProjectId: DEFAULT_PROJECT_ID,
      projects: [{
        id: DEFAULT_PROJECT_ID,
        name: DEFAULT_PROJECT_NAME,
        createdAt: now,
        updatedAt: now,
        data: store
      }]
    };
  }

  function activeProject(portfolio) {
    if (!isPortfolio(portfolio)) return null;
    return portfolio.projects.find((project) => project.id === portfolio.activeProjectId) || portfolio.projects[0] || null;
  }

  function activeProjectData(store) {
    return isPortfolio(store) ? activeProject(store)?.data : store;
  }

  function replaceActiveProjectData(store, data, now = new Date().toISOString()) {
    if (!isPortfolio(store)) return data;
    const project = activeProject(store);
    if (!project) throw new Error("机器人台账当前项目无效。");
    return {
      ...store,
      projects: store.projects.map((item) => item.id === project.id
        ? { ...item, data, updatedAt: now }
        : item)
    };
  }

  return {
    PORTFOLIO_VERSION,
    DEFAULT_PROJECT_ID,
    DEFAULT_PROJECT_NAME,
    isPortfolio,
    wrapLegacyStore,
    activeProject,
    activeProjectData,
    replaceActiveProjectData
  };
}));
