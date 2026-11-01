const base = require('./playwright.config.cjs');

// Scored recoveries and operator admissions need a separate catalogue/database.
// Never reuse or stop the ordinary preview on port 8029.
module.exports = {
  ...base,
  testMatch: 'points.check.cjs',
  outputDir: 'points-results',
  reporter: [['list'], ['html', {open: 'never', outputFolder: 'points-report'}]],
  use: {...base.use, baseURL: 'http://127.0.0.1:8030'},
  webServer: {...base.webServer,
    command: '"' + (process.env.CRUCIBLE_BROWSER_PYTHON || 'python') + '" -B points_server.py',
    url: 'http://127.0.0.1:8030/health',
    timeout: 90000,
  },
};
