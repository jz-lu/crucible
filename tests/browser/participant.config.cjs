const base = require('./playwright.config.cjs');

// A real accepted recovery changes the scheme-wide record. Give this journey
// its own disposable server/database, so it cannot alter other tests' fixtures.
module.exports = {
  ...base,
  testMatch: 'participant-recovery.journey.cjs',
  outputDir: 'participant-results',
  reporter: [['list'], ['html', {open: 'never', outputFolder: 'participant-report'}]],
};
