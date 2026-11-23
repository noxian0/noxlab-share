// Run against a temporary ReceiveServer with password "sample" and a 306 MiB fixture.
const {chromium, devices} = require(process.env.PLAYWRIGHT_CORE || "playwright-core");
const assert = require("node:assert/strict");

(async () => {
    const [url, video] = process.argv.slice(2);
    const browser = await chromium.launch({channel: "chrome", args: ["--no-proxy-server"]});
    try {
        const page = await browser.newPage({...devices["Pixel 5"]});
        await page.addInitScript(() => {
            const send = window.fetch.bind(window);
            let failures = 0;
            let finalReplyLost = false;
            window.fetch = async (url, init) => {
                const response = await send(url, init);
                if (url === "/upload/chunk" && response.ok) {
                    if (init.headers["X-Offset"] === "2097152" && failures++ < 3) {
                        throw new TypeError("Simulated missing reply");
                    }
                    if (!finalReplyLost && (await response.clone().json()).saved) {
                        finalReplyLost = true;
                        throw new TypeError("Simulated missing final reply");
                    }
                }
                return response;
            };
        });
        await page.goto(url);
        await page.locator("#files").setInputFiles(video);
        await page.locator("#password").fill("wrong");
        await page.locator("#upload-button").click();
        await page.getByText("Check the receive password on the PC.").waitFor();
        await page.locator("#password").fill("sample");
        await page.locator("#upload-button").click();
        await page.getByText(/bytes confirmed for this file/).waitFor();
        await page.locator("#upload-button").click();
        await page.getByText(/Saved on PC:/).waitFor({timeout: 120000});
        assert.match(await page.locator("#upload-state").innerText(), /sample\.mp4/);
        await page.locator("#password").fill("sample");
        await page.locator("#files").setInputFiles([
            {name: "empty.txt", mimeType: "text/plain", buffer: Buffer.alloc(0)},
            {name: "empty.txt", mimeType: "text/plain", buffer: Buffer.from("second")}
        ]);
        await page.locator("#upload-button").click();
        await page.getByText("Saved on PC: empty.txt, empty (1).txt").waitFor();
        console.log("Browser picker, password, automatic and manual retry, and batch upload passed.");
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
