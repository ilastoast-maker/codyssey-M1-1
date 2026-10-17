// Optional: npm install --no-save playwright && npx playwright install chromium
// Run from the repository root: node tools/capture_dashboard.cjs
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const python = process.env.PYTHON_EXECUTABLE || (process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const port = process.env.DEMO_PORT || '8502';
  const url = `http://127.0.0.1:${port}`;
  const server = spawn(python, ['-m', 'streamlit', 'run', 'app.py', '--server.headless', 'true', '--server.port', port, '--browser.gatherUsageStats', 'false'], { stdio: 'ignore' });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      try { ready = (await fetch(`${url}/_stcore/health`)).ok; } catch {}
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (!ready) throw new Error('Streamlit did not start');
    browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const page = await browser.newPage({ viewport: { width: 1500, height: 1500 }, deviceScaleFactor: 1 });
    await page.goto(url);
    await page.getByText('1,214', { exact: true }).waitFor();
    await page.locator('[data-testid="stImage"] img').first().waitFor();
    await page.evaluate(() => document.fonts.ready);
    const out = path.join('docs', 'screenshots');
    fs.mkdirSync(out, { recursive: true });
    async function shot(name) {
      await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'));
      await page.screenshot({ path: path.join(out, name), fullPage: true });
    }
    await shot('01_full_period.png');
    await page.getByRole('combobox').click();
    await page.getByRole('option', { name: '2024년', exact: true }).click();
    await page.getByText('242', { exact: true }).waitFor();
    await page.getByText('81.28%', { exact: true }).waitFor();
    await shot('02_year_2024.png');
    const sliders = page.getByRole('slider');
    await sliders.nth(0).focus();
    for (let i = 0; i < 5; i++) await page.keyboard.press('ArrowRight');
    await sliders.nth(1).focus();
    for (let i = 0; i < 60; i++) await page.keyboard.press('ArrowRight');
    await sliders.nth(2).focus();
    for (let i = 0; i < 20; i++) await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => {
      const controls = [...document.querySelectorAll('[role="slider"]')];
      return controls.map(x => x.getAttribute('aria-valuenow')).join(',') === '25,120,40';
    });
    await page.getByRole('tab', { name: '수익률·위험' }).click();
    await shot('03_changed_windows_risk.png');
    await page.getByRole('tab', { name: '거래량', exact: true }).click();
    await page.getByTestId('stTable').waitFor();
    await shot('04_volume_table.png');
    console.log('Captured four dashboard scenarios');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
