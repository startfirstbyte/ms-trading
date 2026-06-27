const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1440, height: 820 });
  await page.goto('http://localhost:5173?symbol=BTCUSD');
  await page.waitForTimeout(2500);
  await page.click('button:has-text("Analyze")');
  await page.waitForTimeout(15000);
  await page.screenshot({ path: 'C:/Users/namnt/Poc/trading/screenshot3.png' });
  await browser.close();
})();
