"use strict";
// Drives the served review page in a real Chrome over the DevTools protocol:
// comment on a line, reply to Claude, submit, then wait for the page to reload with Claude's answer.
// Usage: node live_review.cdp.js <chrome debugging port> <page url>
const [debugPort, pageUrl] = process.argv.slice(2);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pageTarget() {
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
      const page = targets.find((target) => target.type === "page");
      if (page) return page;
    } catch (error) { /* Chrome is still starting */ }
    await sleep(100);
  }
  throw new Error("Chrome did not open a page");
}

async function main() {
  const socket = new WebSocket((await pageTarget()).webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  const waiting = new Map();
  let nextId = 1;
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && waiting.has(message.id)) { waiting.get(message.id)(message); waiting.delete(message.id); }
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    const id = nextId++;
    waiting.set(id, resolve);
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const reply = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (reply.error || reply.result.exceptionDetails) throw new Error(`${expression}\n${JSON.stringify(reply.error || reply.result.exceptionDetails)}`);
    return reply.result.result.value;
  };
  const waitFor = async (expression, what) => {
    for (let attempt = 0; attempt < 150; attempt++) {
      try { if (await evaluate(expression)) return; } catch (error) { /* the page is reloading */ }
      await sleep(100);
    }
    throw new Error(`timed out waiting for ${what}`);
  };
  const write = (text) => evaluate(`(() => {
    const box = document.querySelector(".composer textarea");
    box.value = ${JSON.stringify(text)};
    box.dispatchEvent(new Event("input"));
    document.querySelector(".composer-add").click();
  })()`);

  await send("Page.navigate", { url: pageUrl });
  await waitFor(`document.body.dataset.ready === "1" && document.body.classList.contains("live")`, "the live page");

  await evaluate(`document.querySelector('tr[data-n="10"] .add-comment').click()`);
  await waitFor(`!!document.querySelector(".composer textarea")`, "the composer on line 10");
  await write("Sao không giữ tên cũ?");
  await waitFor(`document.querySelectorAll(".note.draft").length === 1`, "the first pending comment");

  await evaluate(`document.querySelector(".note.comment .reply").click()`);
  await waitFor(`!!document.querySelector(".composer textarea")`, "the composer under Claude's comment");
  await write("Đồng ý, để tôi kiểm tra.");
  await waitFor(`document.querySelectorAll(".note.draft").length === 2 && !document.querySelector(".submit-review").disabled`, "two pending comments");

  await evaluate(`document.querySelector(".submit-review").click()`);
  await waitFor(`!!document.querySelector(".live-banner.waiting") && document.querySelectorAll(".note.draft.was-sent").length === 2`, "the sent state");
  console.log("SUBMITTED");

  // The test now answers as Claude, renders again and restarts the server. The page must notice by itself.
  await waitFor(`!!document.querySelector(".message-claude")`, "Claude's answer after the reload");
  const answer = await evaluate(`document.querySelector(".message-claude").textContent`);
  const pendingLeft = await evaluate(`document.querySelectorAll(".note.draft").length`);
  console.log(JSON.stringify({ answer, pendingLeft }));
  socket.close();
}

main().then(() => process.exit(0), (error) => { console.error(String(error && error.stack || error)); process.exit(1); });
