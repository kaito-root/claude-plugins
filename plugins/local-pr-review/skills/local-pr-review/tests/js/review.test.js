"use strict";
// Pure helpers of the page script, run under Node. No DOM here.
const assert = require("node:assert/strict");
const test = require("node:test");
const review = require("../../assets/review.js");

const ctx = (o, n, s) => ({ t: "ctx", o, n, s });
const del = (o, s) => ({ t: "del", o, n: null, s });
const add = (n, s) => ({ t: "add", o: null, n, s });

test("split view pairs each deleted line with the added line that replaced it", () => {
  const rows = review.pairLines([ctx(1, 1, "a"), del(2, "b"), del(3, "c"), add(2, "B"), ctx(4, 3, "d")]);

  assert.deepEqual(rows.map((row) => [row.left && row.left.o, row.right && row.right.n]), [[1, 1], [2, 2], [3, null], [4, 3]]);
});

test("split view keeps a pure addition on the right only", () => {
  const rows = review.pairLines([add(1, "x"), add(2, "y")]);

  assert.deepEqual(rows.map((row) => [row.left, row.right.n]), [[null, 1], [null, 2]]);
});

test("two separate changes in one hunk are paired separately", () => {
  const rows = review.pairLines([del(1, "a"), add(1, "A"), ctx(2, 2, "m"), del(3, "z"), add(3, "Z")]);

  assert.deepEqual(rows.map((row) => [row.left.o, row.right.n]), [[1, 1], [2, 2], [3, 3]]);
});

test("highlighting never changes the text", () => {
  const samples = [
    ['  def call(user, opts = {}) # says "hi"', "ruby"],
    ["const x = `a ${b}` // c", "js"],
    ['  "key": "value", "n": 12.5', "json"],
    ["select * from users -- all", "sql"],
    ["  name: value # note", "yaml"],
    ["<div class='x'>&amp;</div>", "markup"],
    ["", "ruby"],
  ];
  for (const [text, language] of samples) {
    assert.equal(review.tokenize(text, language).map((token) => token[1]).join(""), text);
  }
});

test("highlighting tells keywords, strings and comments apart", () => {
  const classes = Object.fromEntries(review.tokenize('return "x" if ready? # why', "ruby").filter((t) => t[0]).map((t) => [t[1], t[0]]));

  assert.deepEqual(classes, { return: "kw", '"x"': "str", if: "kw", "# why": "com" });
});

test("a comment marker inside a string stays part of the string", () => {
  const tokens = review.tokenize('url = "http://x" # real', "ruby");

  assert.deepEqual(tokens.filter((t) => t[0] === "str" || t[0] === "com").map((t) => t[1]), ['"http://x"', "# real"]);
});

test("the language comes from the file name", () => {
  assert.deepEqual(["app/a.rb", "Gemfile", "x/y.tsx", "a.py", "b.go", "c.yml", "d.unknown"].map(review.languageOf),
    ["ruby", "ruby", "js", "python", "go", "yaml", null]);
});

test("review text is parsed into paragraphs, lists and code, never into HTML", () => {
  const blocks = review.parseMarkdown("Dùng `with_lock` cho **an toàn**.\n\n- một\n- hai\n\n```ruby\n<b>x</b>\n```");

  assert.deepEqual(blocks.map((block) => block.type), ["p", "ul", "code"]);
  assert.deepEqual(blocks[0].inline, [["text", "Dùng "], ["code", "with_lock"], ["text", " cho "], ["bold", "an toàn"], ["text", "."]]);
  assert.deepEqual(blocks[1].items.map((item) => item[0][1]), ["một", "hai"]);
  assert.equal(blocks[2].text, "<b>x</b>");
});

test("files are grouped by folder for the sidebar", () => {
  const groups = review.groupByFolder([{ path: "app/a.rb" }, { path: "README.md" }, { path: "app/b.rb" }, { path: "app/x/c.rb" }]);

  assert.deepEqual(groups.map((group) => [group.folder, group.files.map((file) => file.path)]),
    [["", ["README.md"]], ["app", ["app/a.rb", "app/b.rb"]], ["app/x", ["app/x/c.rb"]]]);
});

test("notes are indexed by file, side and line, and a range anchors at its last line", () => {
  const analysis = {
    comments: [{ id: "c0", file: "f1", side: "new", line: 12 }, { id: "c1", file: "f1", side: "old", line: 3 }, { id: "c2", file: "f1", side: "new", line: 9, unanchored: true }],
    explanations: [{ id: "e0", file: "f1", side: "new", line: 10, end_line: 12 }],
  };

  const index = review.indexNotes(analysis);

  assert.deepEqual(index.f1.at["new:12"].map((note) => note.id), ["e0", "c0"]);
  assert.deepEqual(index.f1.at["old:3"].map((note) => note.id), ["c1"]);
  assert.deepEqual(index.f1.loose.map((note) => note.id), ["c2"]);
  assert.deepEqual(index.f1.ranges, [{ side: "new", from: 10, to: 12 }]);
});

test("a text in two languages shows the reader's language", () => {
  assert.equal(review.pickText({ vi: "Xin chào", en: "Hello" }, "vi"), "Xin chào");
  assert.equal(review.pickText({ vi: "Xin chào", en: "Hello" }, "en"), "Hello");
});

test("a text written once shows in every language", () => {
  assert.equal(review.pickText("`code`", "vi"), "`code`");
  assert.equal(review.pickText({ en: "Only English" }, "vi"), "Only English");
  assert.equal(review.pickText(null, "en"), "");
});

test("the page starts in Vietnamese only for a Vietnamese browser", () => {
  assert.deepEqual(["vi-VN", "vi", "en-US", "fr", undefined].map(review.defaultLanguage), ["vi", "vi", "en", "en", "en"]);
});

test("every label of the page exists in both languages", () => {
  assert.deepEqual(Object.keys(review.LABELS.vi).sort(), Object.keys(review.LABELS.en).sort());
});

test("a thread of the discussion sits after Claude's notes on its line", () => {
  const analysis = { comments: [{ id: "c0", file: "f1", side: "new", line: 12 }], explanations: [] };
  const discussion = { threads: [{ id: "t1", file: "f1", side: "new", line: 12, messages: [] }, { id: "t2", file: "f2", side: "old", line: 3, messages: [] }] };

  const index = review.indexNotes(analysis, discussion);

  assert.deepEqual(index.f1.at["new:12"].map((note) => [note.id, note.kind]), [["c0", "comment"], ["t1", "thread"]]);
  assert.deepEqual(index.f2.at["old:3"].map((note) => note.id), ["t2"]);
  assert.equal(index.f1.count, 2);
});

test("a submitted review names a thread for a reply and a line for a new comment", () => {
  const drafts = [
    { key: "1", thread: "t1", path: "a.rb", side: "new", line: 4, body: "Chưa rõ." },
    { key: "2", thread: null, path: "a.rb", side: "old", line: 9, re: "Row lock", body: "Vì sao?" },
  ];

  assert.deepEqual(review.submission(drafts), { comments: [
    { thread: "t1", body: "Chưa rõ." },
    { path: "a.rb", side: "old", line: 9, re: "Row lock", body: "Vì sao?" },
  ] });
});
