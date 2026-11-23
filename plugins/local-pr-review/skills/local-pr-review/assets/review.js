(function () {
  "use strict";

  // ---------------------------------------------------------------------------------------
  // Pure helpers. No DOM, so Node can test them.
  // ---------------------------------------------------------------------------------------

  var EXTENSIONS = {
    rb: "ruby", rake: "ruby", gemspec: "ruby", ru: "ruby", jbuilder: "ruby",
    js: "js", jsx: "js", mjs: "js", cjs: "js", ts: "js", tsx: "js",
    py: "python", go: "go", yml: "yaml", yaml: "yaml", json: "json", sql: "sql",
    sh: "shell", bash: "shell", zsh: "shell", css: "css", scss: "css",
    html: "markup", erb: "markup", vue: "markup", xml: "markup", svg: "markup",
    java: "clike", kt: "clike", swift: "clike", c: "clike", h: "clike", cpp: "clike", cc: "clike",
    cs: "clike", rs: "clike", php: "clike", scala: "clike", dart: "clike"
  };
  var FILE_NAMES = { Gemfile: "ruby", Rakefile: "ruby", Dockerfile: "shell", Makefile: "shell" };

  function words(list) {
    var set = Object.create(null);
    list.split(" ").forEach(function (word) { set[word] = true; });
    return set;
  }

  var LANGUAGES = {
    ruby: { comment: "#", symbols: true, keywords: words("def end class module if elsif else unless while until do begin rescue ensure return yield self nil true false and or not then case when in next break raise require require_relative include extend prepend private protected public attr_reader attr_writer attr_accessor lambda proc super for redo retry alias defined? loop") },
    js: { comment: "//", keywords: words("const let var function return if else for while do switch case break continue new class extends super this null undefined true false import export from default async await try catch finally throw typeof instanceof in of void delete yield static get set interface type enum implements public private protected readonly as") },
    python: { comment: "#", keywords: words("def class return if elif else for while in not and or is None True False import from as try except finally raise with lambda yield pass break continue global nonlocal assert async await del self") },
    go: { comment: "//", keywords: words("func return if else for range switch case default break continue go defer chan select struct interface map package import var const type nil true false fallthrough goto") },
    shell: { comment: "#", keywords: words("if then else elif fi for while do done case esac in function return exit export local readonly set unset echo") },
    yaml: { comment: "#", keys: true, keywords: words("true false null yes no on off") },
    json: { keys: true, keywords: words("true false null") },
    sql: { comment: "--", anyCase: true, keywords: words("select from where and or not null is in insert into values update set delete create table alter add drop index unique primary key foreign references join left right inner outer on group by order having limit offset as distinct union all case when then else end begin commit rollback default constraint column exists") },
    css: { keywords: words("important") },
    clike: { comment: "//", keywords: words("if else for while do switch case break continue return class struct enum interface public private protected static final const void int long float double bool boolean char string new null nil true false try catch finally throw throws import package namespace using fn let mut pub impl trait match use func var val override this self super") }
  };

  function languageOf(path) {
    var name = path.split("/").pop();
    if (FILE_NAMES[name]) return FILE_NAMES[name];
    var dot = name.lastIndexOf(".");
    return dot < 0 ? null : EXTENSIONS[name.slice(dot + 1).toLowerCase()] || null;
  }

  function scanner(language) {
    if (language.scanner) return language.scanner;
    var never = "((?!))";
    var comment = language.comment ? "(" + language.comment.replace(/[\/#-]/g, "\\$&") + ".*$)" : never;
    var string = "(\"(?:\\\\.|[^\"\\\\])*\"?|'(?:\\\\.|[^'\\\\])*'?|`(?:\\\\.|[^`\\\\])*`?)";
    var number = "(\\b\\d[\\d_]*(?:\\.\\d+)?\\b)";
    var symbol = language.symbols ? "((?<![:\\w]):[A-Za-z_]\\w*[?!]?)" : never;
    var word = "([A-Za-z_@$][\\w$]*[?!]?)";
    language.scanner = new RegExp([comment, string, number, symbol, word].join("|"), "g");
    return language.scanner;
  }

  function classOfWord(word, language, text, end) {
    var lookup = language.anyCase ? word.toLowerCase() : word;
    if (language.keywords[lookup]) return "kw";
    if (word.charAt(0) === "@") return "var";
    if (/^[A-Z][A-Z0-9_]+$/.test(word)) return "const";
    if (/^[A-Z]/.test(word)) return "type";
    if (text.charAt(end) === "(") return "fn";
    return null;
  }

  // One line of code -> [[class or null, text], ...]. The texts always join back to the line.
  function tokenize(text, languageName) {
    var language = LANGUAGES[languageName];
    if (!text) return [];
    if (!language || text.length > 500) return [[null, text]];
    var tokens = [];
    var regex = scanner(language);
    var last = 0;
    var match;
    regex.lastIndex = 0;
    while ((match = regex.exec(text))) {
      if (match[0] === "") { regex.lastIndex += 1; continue; }
      if (match.index > last) tokens.push([null, text.slice(last, match.index)]);
      var end = match.index + match[0].length;
      var kind = null;
      if (match[1] !== undefined) kind = "com";
      else if (match[2] !== undefined) kind = language.keys && /^\s*:/.test(text.slice(end)) ? "key" : "str";
      else if (match[3] !== undefined) kind = "num";
      else if (match[4] !== undefined) kind = "sym";
      else if (language.keys && /^\s*:(\s|$)/.test(text.slice(end))) kind = "key";
      else kind = classOfWord(match[0], language, text, end);
      tokens.push([kind, match[0]]);
      last = end;
    }
    if (last < text.length) tokens.push([null, text.slice(last)]);
    return tokens;
  }

  // Lines of one hunk -> rows for the side-by-side view. A run of deletions is paired, in order,
  // with the run of additions that follows it.
  function pairLines(lines) {
    var rows = [];
    var position = 0;
    while (position < lines.length) {
      if (lines[position].t === "ctx") {
        rows.push({ left: lines[position], right: lines[position] });
        position += 1;
        continue;
      }
      var removed = [];
      var added = [];
      while (position < lines.length && lines[position].t === "del") removed.push(lines[position++]);
      while (position < lines.length && lines[position].t === "add") added.push(lines[position++]);
      for (var i = 0; i < Math.max(removed.length, added.length); i++) {
        rows.push({ left: removed[i] || null, right: added[i] || null });
      }
    }
    return rows;
  }

  function parseInline(text) {
    var parts = [];
    var regex = /`([^`]+)`|\*\*([^*]+)\*\*/g;
    var last = 0;
    var match;
    while ((match = regex.exec(text))) {
      if (match.index > last) parts.push(["text", text.slice(last, match.index)]);
      parts.push(match[1] !== undefined ? ["code", match[1]] : ["bold", match[2]]);
      last = match.index + match[0].length;
    }
    if (last < text.length) parts.push(["text", text.slice(last)]);
    return parts;
  }

  // A small, safe subset of Markdown -> blocks. The page turns blocks into DOM nodes with
  // textContent, so review text can never become markup.
  function parseMarkdown(text) {
    var blocks = [];
    var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    var position = 0;
    function listItem(line) { return /^\s*(?:[-*]|\d+[.)])\s+(.*)$/.exec(line); }
    while (position < lines.length) {
      var line = lines[position];
      if (!line.trim()) { position += 1; continue; }
      var fence = /^\s*```\s*([\w+-]*)\s*$/.exec(line);
      if (fence) {
        var code = [];
        position += 1;
        while (position < lines.length && !/^\s*```\s*$/.test(lines[position])) code.push(lines[position++]);
        position += 1;
        blocks.push({ type: "code", language: fence[1] || null, text: code.join("\n") });
        continue;
      }
      if (listItem(line)) {
        var ordered = /^\s*\d/.test(line);
        var items = [];
        while (position < lines.length && listItem(lines[position])) items.push(parseInline(listItem(lines[position++])[1]));
        blocks.push({ type: ordered ? "ol" : "ul", items: items });
        continue;
      }
      var paragraph = [];
      while (position < lines.length && lines[position].trim() && !listItem(lines[position]) && !/^\s*```/.test(lines[position])) {
        paragraph.push(lines[position++].trim());
      }
      blocks.push({ type: "p", inline: parseInline(paragraph.join("\n")) });
    }
    return blocks;
  }

  function groupByFolder(files) {
    var folders = Object.create(null);
    files.forEach(function (file) {
      var cut = file.path.lastIndexOf("/");
      var folder = cut < 0 ? "" : file.path.slice(0, cut);
      (folders[folder] = folders[folder] || []).push(file);
    });
    return Object.keys(folders).sort().map(function (folder) {
      return { folder: folder, files: folders[folder].sort(function (a, b) { return a.path < b.path ? -1 : 1; }) };
    });
  }

  // analysis -> { fileId: { at: {"side:line": [notes]}, loose: [notes], ranges: [...], count } }
  function indexNotes(analysis) {
    var index = Object.create(null);
    function bucket(fileId) {
      return (index[fileId] = index[fileId] || { at: Object.create(null), loose: [], ranges: [], count: 0 });
    }
    function place(note, kind) {
      if (!note.file) return;
      var entry = bucket(note.file);
      note.kind = kind;
      entry.count += 1;
      if (note.unanchored || !note.line) { entry.loose.push(note); return; }
      var key = note.side + ":" + (note.end_line || note.line);
      (entry.at[key] = entry.at[key] || []).push(note);
      if (kind === "explanation") entry.ranges.push({ side: note.side, from: note.line, to: note.end_line || note.line });
    }
    ((analysis && analysis.explanations) || []).forEach(function (note) { place(note, "explanation"); });
    ((analysis && analysis.comments) || []).forEach(function (note) { place(note, "comment"); });
    return index;
  }

  // A review text is one string, or {vi, en}. A text written once shows in every language.
  function pickText(value, language) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    return value[language] || value.en || value.vi || "";
  }

  function defaultLanguage(browserLanguage) {
    return /^vi\b/i.test(String(browserLanguage || "")) ? "vi" : "en";
  }

  function plural(count, one, many) { return count + " " + (count === 1 ? one : many); }

  // Every label the page itself shows. Git and review terms stay in English in both.
  var LABELS = {
    en: {
      changedFiles: "Changed files",
      filterFiles: "Filter files  ( / )",
      onlyNotes: "Only files with notes",
      noMatch: "No file matches.",
      repositoryRoot: "(repository root)",
      filesChanged: function (n) { return plural(n, "file", "files") + " changed"; },
      chipBranch: "branch", chipScope: "scope", chipBase: "base", chipTarget: "target", chipUntracked: "untracked", chipCollected: "collected",
      included: "included", emptyTree: "empty tree",
      baseHint: "No commit to compare with, so everything counts as new",
      overall: "Overall change", behavior: "Key behavioral changes", risks: "Potential risks", tests: "Suggested tests",
      comments: function (n) { return "Review comments (" + n + ")"; },
      flows: "Architecture and execution flow", before: "Before", after: "After", changes: "Changes",
      what: "What changed?", why: "Why does it matter?", how: "How does it work?", improvements: "Suggested improvements", fix: "Suggested fix",
      confirmed: "confirmed", inferred: "inferred",
      confirmedHint: "Backed by code or tests in the repository",
      inferredHint: "Claude's reading of the intent, not confirmed by the code",
      line: function (n) { return "line " + n; },
      lines: function (from, to) { return "lines " + from + "–" + to; },
      oldSide: " (old)",
      showLines: function (n) { return "↕ Show " + plural(n, "unchanged line", "unchanged lines"); },
      farLines: function (n) { return plural(n, "unchanged line", "unchanged lines") + " further away are not included in this page"; },
      skippedLines: function (n) { return "⋯ " + plural(n, "unchanged line", "unchanged lines") + " (not included in this page)"; },
      noNewline: "No newline at end of file",
      binaryFile: "Binary file. There is no text diff to show.",
      renamedOnly: "Renamed without changes.",
      noTextChanges: "No text changes (empty file or mode change only).",
      cutHere: function (shown, total) { return "Cut here: " + shown + " of " + total + " diff lines are shown."; },
      excludedSensitive: "Content left out: this file usually holds credentials.",
      excludedSize: "Content left out: the review reached its size limit.",
      excludedNotFile: "Content left out: not a regular file.",
      looseNotes: "Notes without a line in this diff",
      tagUntracked: "untracked", tagBinary: "binary", tagGenerated: "generated", tagCut: "cut",
      notes: function (n) { return plural(n, "note", "notes"); },
      viewed: "Viewed", toggleFile: "Collapse or expand this file",
      unified: "Unified", split: "Split", wrap: "Wrap", wrapHint: "Wrap long lines",
      aiNotes: "AI notes", aiNotesHint: "Show or hide Claude's notes inside the diff",
      foldAll: "Fold all", foldAllHint: "Expand or collapse every file",
      theme: "Theme", themeHint: "Switch between light and dark",
      languageHint: "Language of the page and of the review",
      show: function (n) { return "Show " + n; },
      noAnalysis: "No AI analysis was attached to this review. The page shows the diff only.",
      untrackedLeftOut: function (n) { return plural(n, "untracked file is", "untracked files are") + " NOT part of this review. Run again with --include-untracked or --untracked-path to add them."; },
      truncated: function (n, limit) { return plural(n, "file is", "files are") + " cut at " + limit + " diff lines."; },
      omitted: function (n, limit) { return plural(n, "file is", "files are") + " listed without content: the review reached " + limit + " diff lines."; },
      sensitive: function (n) { return plural(n, "file that usually holds credentials is", "files that usually hold credentials are") + " listed without content."; },
      redactions: function (n) { return plural(n, "value that looks like a secret was", "values that look like secrets were") + " redacted in the code below."; }
    },
    vi: {
      changedFiles: "File thay đổi",
      filterFiles: "Lọc file  ( / )",
      onlyNotes: "Chỉ file có ghi chú",
      noMatch: "Không có file nào khớp.",
      repositoryRoot: "(thư mục gốc)",
      filesChanged: function (n) { return n + " file thay đổi"; },
      chipBranch: "branch", chipScope: "phạm vi", chipBase: "base", chipTarget: "target", chipUntracked: "untracked", chipCollected: "thu thập lúc",
      included: "đã gồm", emptyTree: "cây rỗng",
      baseHint: "Không có commit để so sánh, nên mọi thứ được tính là mới",
      overall: "Tổng quan thay đổi", behavior: "Thay đổi hành vi chính", risks: "Rủi ro tiềm ẩn", tests: "Test nên bổ sung",
      comments: function (n) { return "Nhận xét review (" + n + ")"; },
      flows: "Kiến trúc và luồng thực thi", before: "Trước", after: "Sau", changes: "Các thay đổi",
      what: "Thay đổi gì?", why: "Vì sao quan trọng?", how: "Hoạt động thế nào?", improvements: "Đề xuất cải thiện", fix: "Cách sửa đề xuất",
      confirmed: "đã xác nhận", inferred: "suy luận",
      confirmedHint: "Có code hoặc test trong repository chứng minh",
      inferredHint: "Claude suy ra ý định, code chưa xác nhận",
      line: function (n) { return "dòng " + n; },
      lines: function (from, to) { return "dòng " + from + "–" + to; },
      oldSide: " (cũ)",
      showLines: function (n) { return "↕ Hiện " + n + " dòng không đổi"; },
      farLines: function (n) { return n + " dòng không đổi ở xa hơn không có trong trang này"; },
      skippedLines: function (n) { return "⋯ " + n + " dòng không đổi (không có trong trang này)"; },
      noNewline: "Không có ký tự xuống dòng ở cuối file",
      binaryFile: "File binary. Không có diff dạng text để hiển thị.",
      renamedOnly: "Chỉ đổi tên, nội dung không đổi.",
      noTextChanges: "Không có thay đổi dạng text (file rỗng hoặc chỉ đổi mode).",
      cutHere: function (shown, total) { return "Cắt tại đây: hiển thị " + shown + " trên " + total + " dòng diff."; },
      excludedSensitive: "Không hiển thị nội dung: file này thường chứa credentials.",
      excludedSize: "Không hiển thị nội dung: review đã chạm giới hạn kích thước.",
      excludedNotFile: "Không hiển thị nội dung: không phải file thường.",
      looseNotes: "Ghi chú không gắn với dòng nào trong diff",
      tagUntracked: "untracked", tagBinary: "binary", tagGenerated: "generated", tagCut: "bị cắt",
      notes: function (n) { return n + " ghi chú"; },
      viewed: "Đã xem", toggleFile: "Thu gọn hoặc mở file này",
      unified: "Unified", split: "Split", wrap: "Wrap", wrapHint: "Tự xuống dòng với dòng dài",
      aiNotes: "Ghi chú AI", aiNotesHint: "Hiện hoặc ẩn ghi chú của Claude trong diff",
      foldAll: "Thu gọn hết", foldAllHint: "Mở hoặc thu gọn mọi file",
      theme: "Giao diện", themeHint: "Chuyển giữa sáng và tối",
      languageHint: "Ngôn ngữ của trang và của bài review",
      show: function (n) { return "Xem " + n; },
      noAnalysis: "Review này không có phần phân tích của AI. Trang chỉ hiển thị diff.",
      untrackedLeftOut: function (n) { return n + " file untracked KHÔNG nằm trong review này. Chạy lại với --include-untracked hoặc --untracked-path để thêm."; },
      truncated: function (n, limit) { return n + " file bị cắt ở " + limit + " dòng diff."; },
      omitted: function (n, limit) { return n + " file chỉ được liệt kê, không có nội dung: review đã chạm " + limit + " dòng diff."; },
      sensitive: function (n) { return n + " file thường chứa credentials chỉ được liệt kê, không có nội dung."; },
      redactions: function (n) { return n + " giá trị trông giống secret đã được che trong code bên dưới."; }
    }
  };

  var api = { languageOf: languageOf, tokenize: tokenize, pairLines: pairLines, parseMarkdown: parseMarkdown, groupByFolder: groupByFolder, indexNotes: indexNotes, pickText: pickText, defaultLanguage: defaultLanguage, LABELS: LABELS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined") return;

  // ---------------------------------------------------------------------------------------
  // The page
  // ---------------------------------------------------------------------------------------

  var data = JSON.parse(document.getElementById("review-data").textContent);
  var changes = data.changes;
  var analysis = data.analysis;
  var notes = indexNotes(analysis);
  var SEVERITY_ORDER = ["critical", "major", "minor", "nit", "question"];
  var STATUS_LETTER = { added: "A", modified: "M", deleted: "D", renamed: "R", copied: "C", typechange: "T", unmerged: "U" };
  var EXCLUDED_LABEL = { "sensitive": "excludedSensitive", "size-limit": "excludedSize", "not-a-file": "excludedNotFile" };
  var SVG_NS = "http://www.w3.org/2000/svg";
  var storageKey = "local-pr-review:" + changes.repo.root;
  var fileStates = [];
  var collapsedById = Object.create(null);
  var searchBox = null;
  var observer = null;
  var state = { view: "unified", wrap: false, notes: true, theme: null, lang: "en", all: false };

  // label("showLines", 3) -> the label in the reader's language
  function label(key) {
    var entry = LABELS[state.lang][key];
    return typeof entry === "function" ? entry.apply(null, Array.prototype.slice.call(arguments, 1)) : entry;
  }
  function own(value) { return pickText(value, state.lang); }

  function remember(key, value) {
    try { window.localStorage.setItem(storageKey + ":" + key, JSON.stringify(value)); } catch (error) { /* private window */ }
  }
  function recall(key, fallback) {
    try {
      var stored = window.localStorage.getItem(storageKey + ":" + key);
      return stored === null ? fallback : JSON.parse(stored);
    } catch (error) { return fallback; }
  }

  // h("div", {class: "x", onclick: fn, "data-id": 1}, child, "text", [more children])
  function h(tag, props) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (key) {
      var value = props[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key.slice(0, 2) === "on") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    });
    append(node, Array.prototype.slice.call(arguments, 2));
    return node;
  }
  function append(node, children) {
    children.forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      if (Array.isArray(child)) append(node, child);
      else node.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
    });
  }
  function svg(tag, attributes) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attributes || {}).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    append(node, Array.prototype.slice.call(arguments, 2));
    return node;
  }

  // Review text comes in as a string or {vi, en}. These three turn it into nodes, never into markup.
  function inlineText(text) { return inlineNodes(parseInline(own(text))); }
  function plainText(text) { return own(text).replace(/`|\*\*/g, ""); }
  function inlineNodes(parts) {
    return parts.map(function (part) {
      if (part[0] === "code") return h("code", null, part[1]);
      if (part[0] === "bold") return h("strong", null, part[1]);
      return part[1];
    });
  }
  function markdown(text) {
    return h("div", { class: "md" }, parseMarkdown(own(text)).map(function (block) {
      if (block.type === "code") return h("pre", null, h("code", null, codeNodes(block.text, block.language)));
      if (block.type === "p") return h("p", null, inlineNodes(block.inline));
      return h(block.type, null, block.items.map(function (item) { return h("li", null, inlineNodes(item)); }));
    }));
  }
  function codeNodes(text, language) {
    var known = { rb: "ruby", ruby: "ruby", js: "js", ts: "js", javascript: "js", typescript: "js", py: "python", python: "python", go: "go", sql: "sql", yaml: "yaml", yml: "yaml", json: "json", sh: "shell", bash: "shell" }[language];
    return text.split("\n").map(function (line, number) { return [number ? "\n" : null, highlighted(line, known)]; });
  }
  function highlighted(text, language) {
    return tokenize(text, language).map(function (token) {
      return token[0] ? h("span", { class: "t-" + token[0] }, token[1]) : token[1];
    });
  }
  function short(sha) { return sha ? sha.slice(0, 10) : label("emptyTree"); }

  // -- summary ----------------------------------------------------------------------------

  function chip(name, value, title) {
    return h("span", { class: "chip", title: title }, h("span", { class: "chip-label" }, name), value);
  }
  function counts(additions, deletions) {
    return h("span", { class: "counts" }, h("span", { class: "plus" }, "+" + additions), " ", h("span", { class: "minus" }, "−" + deletions));
  }
  function listCard(title, items, kind) {
    if (!items || !items.length) return null;
    return h("section", { class: "card " + kind }, h("h3", null, title), h("ul", null, items.map(function (item) {
      return h("li", null, inlineText(item));
    })));
  }
  function notice(kind, text, details) {
    return h("div", { class: "notice " + kind }, text, details && details.length ? h("details", null, h("summary", null, label("show", details.length)), h("ul", null, details.map(function (item) { return h("li", null, h("code", null, item)); }))) : null);
  }

  function notices() {
    var limits = changes.limits;
    var list = changes.warnings.map(function (warning) { return notice("warn", warning); });
    if (!analysis) list.push(notice("warn", label("noAnalysis")));
    if (changes.untracked_not_included_count) list.push(notice("warn", label("untrackedLeftOut", changes.untracked_not_included_count), changes.untracked_not_included));
    if (limits.truncated_files.length) list.push(notice("warn", label("truncated", limits.truncated_files.length, limits.max_file_lines), limits.truncated_files));
    if (limits.omitted_files.length) list.push(notice("warn", label("omitted", limits.omitted_files.length, limits.max_total_lines), limits.omitted_files));
    if (limits.sensitive_files.length) list.push(notice("info", label("sensitive", limits.sensitive_files.length), limits.sensitive_files));
    if (changes.redactions) list.push(notice("info", label("redactions", changes.redactions)));
    return list;
  }

  function findingsCard() {
    var comments = ((analysis && analysis.comments) || []).slice().sort(function (a, b) {
      return SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity);
    });
    if (!comments.length) return null;
    return h("section", { class: "card findings" }, h("h3", null, label("comments", comments.length)), h("ul", { class: "finding-list" }, comments.map(function (comment) {
      return h("li", null, h("button", { class: "finding", type: "button", onclick: function () { jumpToNote(comment); } },
        h("span", { class: "badge sev-" + comment.severity }, comment.severity),
        h("span", { class: "finding-title" }, inlineText(comment.title)),
        h("span", { class: "finding-place" }, comment.path + ":" + comment.line)));
    })));
  }

  function sequenceDiagram(sequence) {
    var gap = 220, left = 110, top = 46, step = 46;
    var width = left * 2 + gap * Math.max(sequence.actors.length - 1, 0);
    var height = top + step * sequence.messages.length + 40;
    function x(actor) { return left + gap * sequence.actors.indexOf(actor); }
    var picture = svg("svg", { class: "sequence", viewBox: "0 0 " + width + " " + height, width: width, height: height, role: "img" });
    sequence.actors.forEach(function (actor) {
      picture.appendChild(svg("line", { class: "lifeline", x1: x(actor), y1: top - 8, x2: x(actor), y2: height - 12 }));
      picture.appendChild(svg("rect", { class: "actor", x: x(actor) - 82, y: 6, width: 164, height: 30, rx: 6 }));
      picture.appendChild(svg("text", { class: "actor-name", x: x(actor), y: 26, "text-anchor": "middle" }, actor.length > 24 ? actor.slice(0, 23) + "…" : actor));
    });
    sequence.messages.forEach(function (message, number) {
      var y = top + step * number + 30;
      var from = x(message.from), to = x(message.to);
      var kind = "message " + message.kind;
      if (from === to) {
        picture.appendChild(svg("path", { class: kind, d: "M" + from + " " + (y - 12) + " h34 v16 h-28", fill: "none" }));
        picture.appendChild(svg("path", { class: "arrowhead " + message.kind, d: "M" + (from + 2) + " " + (y + 4) + " l8 -4 v8 z" }));
      } else {
        var direction = to > from ? 1 : -1;
        picture.appendChild(svg("line", { class: kind, x1: from, y1: y, x2: to - 8 * direction, y2: y }));
        picture.appendChild(svg("path", { class: "arrowhead " + message.kind, d: "M" + to + " " + y + " l" + (-9 * direction) + " -4 v8 z" }));
      }
      var middle = from === to ? from + 44 : (from + to) / 2;
      var room = from === to ? 24 : Math.max(Math.floor(Math.abs(to - from) / 6.3) - 2, 8);
      var text = (number + 1) + ". " + plainText(message.label);
      picture.appendChild(svg("text", { class: "message-label", x: middle, y: y - 7, "text-anchor": from === to ? "start" : "middle" }, text.length > room ? text.slice(0, room - 1) + "…" : text));
    });
    return h("div", { class: "sequence-wrap" }, picture, h("ol", { class: "sequence-steps" }, sequence.messages.map(function (message) {
      return h("li", null, h("span", { class: "step-route" }, message.from + " → " + message.to), " ", inlineText(message.label), message.kind === "call" ? null : h("span", { class: "tag" }, message.kind));
    })));
  }

  function flowCard(flow) {
    function steps(title, list, kind) {
      if (!list.length) return null;
      return h("div", { class: "flow-steps " + kind }, h("h4", null, title), h("ol", null, list.map(function (step) { return h("li", null, inlineText(step)); })));
    }
    return h("section", { class: "card flow" }, h("h3", null, inlineText(flow.title)), flow.description ? markdown(flow.description) : null,
      h("div", { class: "flow-columns" }, steps(label("before"), flow.before, "before"), steps(label("after"), flow.after, "after")),
      flow.sequence && flow.sequence.actors.length ? sequenceDiagram(flow.sequence) : null);
  }

  function summary() {
    var scope = changes.scope, stats = changes.stats;
    var target = scope.mode === "commit" ? "commit " + scope.target : scope.target;
    return h("section", { class: "summary", id: "summary" },
      h("p", { class: "eyebrow" }, "Local PR Review · ", changes.repo.name),
      h("h1", null, analysis ? inlineText(analysis.title) : changes.repo.branch),
      h("div", { class: "chips" },
        chip(label("chipBranch"), changes.repo.branch),
        chip(label("chipScope"), scope.label),
        chip(label("chipBase"), short(scope.base_commit), scope.base_commit || label("baseHint")),
        chip(label("chipTarget"), target),
        scope.include_untracked ? chip(label("chipUntracked"), label("included")) : null,
        chip(label("chipCollected"), changes.generated_at.replace("T", " ").slice(0, 16))),
      h("div", { class: "stats" }, h("strong", null, label("filesChanged", stats.files)), counts(stats.additions, stats.deletions)),
      notices(),
      analysis ? [
        own(analysis.overview) ? h("section", { class: "card overview" }, h("h3", null, label("overall")), markdown(analysis.overview)) : null,
        h("div", { class: "card-grid" },
          listCard(label("behavior"), analysis.behavior_changes, "behavior"),
          listCard(label("risks"), analysis.risks, "risks"),
          listCard(label("tests"), analysis.suggested_tests, "tests")),
        findingsCard(),
        analysis.flows.length ? h("h2", { class: "section-title" }, label("flows")) : null,
        analysis.flows.map(flowCard)
      ] : null);
  }

  // -- notes next to the code -----------------------------------------------------------------

  function lineLabel(note) {
    var lines = note.end_line && note.end_line !== note.line ? label("lines", note.line, note.end_line) : label("line", note.line);
    return lines + (note.side === "old" ? label("oldSide") : "");
  }
  function explainPart(title, text, extra) {
    return own(text) ? h("div", { class: "explain-part" }, h("h5", null, title, extra || null), markdown(text)) : null;
  }
  function noteCard(note) {
    if (note.kind === "comment") {
      return h("article", { class: "note comment sev-" + note.severity, id: "note-" + note.id },
        h("header", null, h("span", { class: "badge sev-" + note.severity }, note.severity), h("strong", null, inlineText(note.title)), h("span", { class: "note-place" }, lineLabel(note))),
        markdown(note.body),
        own(note.suggestion) ? h("div", { class: "suggestion" }, h("h5", null, label("fix")), markdown(note.suggestion)) : null);
    }
    return h("details", { class: "note explain", id: "note-" + note.id, open: true },
      h("summary", null, h("span", { class: "badge explain-badge" }, "Claude"), h("strong", null, inlineText(note.title)), h("span", { class: "note-place" }, lineLabel(note))),
      explainPart(label("what"), note.what),
      explainPart(label("why"), note.why, h("span", { class: "tag basis-" + note.why_basis, title: label(note.why_basis + "Hint") }, label(note.why_basis))),
      explainPart(label("how"), note.how),
      explainPart(label("risks"), note.risks),
      explainPart(label("improvements"), note.improvements));
  }
  function threadRow(list, columns) {
    return h("tr", { class: "thread" }, h("td", { colspan: columns }, h("div", { class: "thread-inner" }, list.map(noteCard))));
  }

  // -- diff rows ------------------------------------------------------------------------------

  function codeCell(line, language, side) {
    if (!line) return h("td", { class: "code empty" });
    var marker = line.t === "add" ? "+" : line.t === "del" ? "-" : " ";
    return h("td", { class: "code " + (side || "") + " " + line.t },
      h("span", { class: "marker" }, marker),
      h("span", { class: "text" }, highlighted(line.s, language)),
      line.nonl ? h("span", { class: "nonl", title: label("noNewline") }, " ⏎̸") : null);
  }
  function numberCell(number) { return h("td", { class: "num" }, number === null || number === undefined ? "" : number); }

  function inRange(fileNotes, side, number) {
    return number !== null && fileNotes.ranges.some(function (range) { return range.side === side && number >= range.from && number <= range.to; });
  }

  // Rows for a list of lines, each followed by the notes anchored to it.
  function lineRows(lines, context) {
    var rows = [];
    var columns = context.view === "split" ? 4 : 3;
    function emit(row, left, right) {
      var keys = [];
      if (left && left.o !== null) { row.setAttribute("data-o", left.o); keys.push("old:" + left.o); }
      if (right && right.n !== null) { row.setAttribute("data-n", right.n); keys.push("new:" + right.n); }
      if ((left && inRange(context.notes, "old", left.o)) || (right && inRange(context.notes, "new", right.n))) row.setAttribute("data-range", "1");
      rows.push(row);
      var attached = [];
      keys.forEach(function (key) { attached = attached.concat(context.notes.at[key] || []); });
      if (attached.length) rows.push(threadRow(attached, columns));
    }
    if (context.view === "split") {
      pairLines(lines).forEach(function (pair) {
        var kind = pair.left && pair.right ? (pair.left.t === "ctx" ? "ctx" : "change") : pair.left ? "del" : "add";
        emit(h("tr", { class: "row " + kind },
          numberCell(pair.left && pair.left.o), codeCell(pair.left, context.language, "left"),
          numberCell(pair.right && pair.right.n), codeCell(pair.right, context.language, "right")),
        pair.left && pair.left.t !== "add" ? pair.left : null, pair.right && pair.right.t !== "del" ? pair.right : null);
      });
      return rows;
    }
    lines.forEach(function (line) {
      emit(h("tr", { class: "row " + line.t }, numberCell(line.o), numberCell(line.n), codeCell(line, context.language)),
        line.t === "add" ? null : line, line.t === "del" ? null : line);
    });
    return rows;
  }

  function gapLines(segment) {
    return segment.lines.map(function (text, offset) { return { t: "ctx", o: segment.o + offset, n: segment.n + offset, s: text }; });
  }
  function gapHasNotes(segment, fileNotes) {
    return gapLines(segment).some(function (line) { return fileNotes.at["new:" + line.n] || fileNotes.at["old:" + line.o]; });
  }
  function gapRows(segment, context) {
    var columns = context.view === "split" ? 4 : 3;
    var hidden = segment.lines.length;
    if (!hidden) return [h("tr", { class: "gap" }, h("td", { colspan: columns }, h("span", { class: "gap-text" }, label("skippedLines", segment.skipped))))];
    if (gapHasNotes(segment, context.notes)) return lineRows(gapLines(segment), context);
    var row = h("tr", { class: "gap" });
    var button = h("button", { type: "button", class: "gap-button", onclick: function () {
      var fragment = document.createDocumentFragment();
      lineRows(gapLines(segment), context).forEach(function (line) { fragment.appendChild(line); });
      row.parentNode.replaceChild(fragment, row);
    } }, label("showLines", hidden));
    row.appendChild(h("td", { colspan: columns }, button, segment.skipped ? h("span", { class: "gap-text" }, " · " + label("farLines", segment.skipped)) : null));
    return [row];
  }
  function hunkHeader(hunk, columns) {
    var range = "@@ -" + hunk.old_start + "," + hunk.old_count + " +" + hunk.new_start + "," + hunk.new_count + " @@";
    return h("tr", { class: "hunk" }, h("td", { colspan: columns }, range, hunk.section ? h("span", { class: "hunk-section" }, " " + hunk.section) : null));
  }

  function diffTable(file) {
    // A file that only exists on one side would leave half of a side-by-side table empty.
    var view = file.status === "added" || file.status === "deleted" ? "unified" : state.view;
    var context = { view: view, language: languageOf(file.path), notes: notes[file.id] || { at: {}, loose: [], ranges: [], count: 0 } };
    var columns = view === "split" ? 4 : 3;
    var body = h("tbody");
    file.segments.forEach(function (segment) {
      var rows = segment.kind === "gap" ? gapRows(segment, context) : [hunkHeader(segment, columns)].concat(lineRows(segment.lines, context));
      rows.forEach(function (row) { body.appendChild(row); });
    });
    var columnsGroup = view === "split"
      ? h("colgroup", null, h("col", { class: "col-num" }), h("col", { class: "col-code" }), h("col", { class: "col-num" }), h("col", { class: "col-code" }))
      : h("colgroup", null, h("col", { class: "col-num" }), h("col", { class: "col-num" }), h("col", { class: "col-code" }));
    return h("table", { class: "diff " + view }, columnsGroup, body);
  }

  // -- one file -------------------------------------------------------------------------------

  function fileBody(file) {
    var fileNotes = notes[file.id];
    var parts = [];
    var summaryText = analysis && analysis.files[file.id];
    if (own(summaryText)) parts.push(h("div", { class: "file-summary" }, h("span", { class: "badge explain-badge" }, "Claude"), markdown(summaryText)));
    if (fileNotes && fileNotes.loose.length) parts.push(h("div", { class: "loose-notes" }, h("p", { class: "loose-title" }, label("looseNotes")), fileNotes.loose.map(noteCard)));
    if (file.excluded) parts.push(h("p", { class: "placeholder" }, label(EXCLUDED_LABEL[file.excluded])));
    else if (file.binary) parts.push(h("p", { class: "placeholder" }, label("binaryFile")));
    else if (!file.segments.length) parts.push(h("p", { class: "placeholder" }, label(file.status === "renamed" ? "renamedOnly" : "noTextChanges")));
    else parts.push(h("div", { class: "diff-wrap" }, diffTable(file)));
    if (file.truncated) parts.push(h("p", { class: "placeholder" }, label("cutHere", file.truncated.shown, file.truncated.total)));
    return parts;
  }

  function renderFile(fileState) {
    while (fileState.body.firstChild) fileState.body.removeChild(fileState.body.firstChild);
    append(fileState.body, fileBody(fileState.file));
    fileState.rendered = true;
  }
  function setCollapsed(fileState, collapsed) {
    fileState.collapsed = collapsed;
    collapsedById[fileState.file.id] = collapsed;
    fileState.section.classList.toggle("collapsed", collapsed);
    fileState.toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    if (!collapsed && !fileState.rendered) renderFile(fileState);
  }

  function fileSection(file) {
    var fileNotes = notes[file.id];
    var viewedKey = "viewed:" + changes.generated_at + ":" + file.path;
    var fileState = { file: file, rendered: false, collapsed: false };
    fileState.body = h("div", { class: "file-body" });
    fileState.toggle = h("button", { class: "file-toggle", type: "button", "aria-label": label("toggleFile"), onclick: function () { setCollapsed(fileState, !fileState.collapsed); } }, "▾");
    var viewed = h("input", { type: "checkbox", onchange: function () {
      remember(viewedKey, viewed.checked);
      fileState.section.classList.toggle("viewed", viewed.checked);
      fileState.item.classList.toggle("viewed", viewed.checked);
      setCollapsed(fileState, viewed.checked);
    } });
    fileState.section = h("section", { class: "file", id: "file-" + file.id, "data-path": file.path },
      h("header", { class: "file-head" }, fileState.toggle,
        h("span", { class: "status status-" + file.status, title: file.status }, STATUS_LETTER[file.status] || "M"),
        h("span", { class: "file-path" }, file.old_path ? [h("span", { class: "old-path" }, file.old_path), " → "] : null, file.path),
        file.untracked ? h("span", { class: "tag" }, label("tagUntracked")) : null,
        file.binary ? h("span", { class: "tag" }, label("tagBinary")) : null,
        file.collapsed ? h("span", { class: "tag" }, label("tagGenerated")) : null,
        file.truncated ? h("span", { class: "tag warn" }, label("tagCut")) : null,
        fileNotes ? h("span", { class: "tag notes-tag" }, label("notes", fileNotes.count)) : null,
        h("span", { class: "spacer" }),
        counts(file.additions, file.deletions),
        h("label", { class: "viewed-label" }, viewed, " " + label("viewed"))),
      fileState.body);
    fileState.startsCollapsed = Boolean(file.collapsed || file.excluded) && !fileNotes;
    fileState.viewedBox = viewed;
    fileState.viewedKey = viewedKey;
    return fileState;
  }

  function reveal(fileState) {
    setCollapsed(fileState, false);
    fileState.section.scrollIntoView({ block: "start" });
  }
  function jumpToNote(note) {
    var fileState = fileStates.filter(function (candidate) { return candidate.file.id === note.file; })[0];
    if (!fileState) return;
    setCollapsed(fileState, false);
    state.notes = true;
    document.body.classList.remove("hide-notes");
    var card = document.getElementById("note-" + note.id);
    if (!card) return;
    if (card.tagName === "DETAILS") card.open = true;
    card.scrollIntoView({ block: "center" });
    card.classList.add("flash");
    window.setTimeout(function () { card.classList.remove("flash"); }, 1600);
  }

  // -- sidebar --------------------------------------------------------------------------------

  function worstSeverity(fileId) {
    var worst = null;
    ((analysis && analysis.comments) || []).forEach(function (comment) {
      if (comment.file === fileId && (worst === null || SEVERITY_ORDER.indexOf(comment.severity) < SEVERITY_ORDER.indexOf(worst))) worst = comment.severity;
    });
    return worst;
  }
  function sidebar() {
    var search = h("input", { type: "search", class: "file-search", placeholder: label("filterFiles"), "aria-label": label("filterFiles") });
    var onlyNotes = h("input", { type: "checkbox" });
    var empty = h("p", { class: "no-match", hidden: true }, label("noMatch"));
    var groups = groupByFolder(changes.files).map(function (group) {
      var items = group.files.map(function (file) {
        var fileState = fileStates.filter(function (candidate) { return candidate.file === file; })[0];
        var fileNotes = notes[file.id];
        var severity = worstSeverity(file.id);
        fileState.item = h("li", null, h("button", { type: "button", class: "tree-file", title: file.path, onclick: function () { reveal(fileState); } },
          h("span", { class: "status status-" + file.status }, STATUS_LETTER[file.status] || "M"),
          h("span", { class: "tree-name" }, file.path.split("/").pop()),
          fileNotes ? h("span", { class: "dot " + (severity ? "sev-" + severity : "explain-dot"), title: label("notes", fileNotes.count) }, fileNotes.count) : null,
          counts(file.additions, file.deletions)));
        return fileState;
      });
      var node = h("details", { class: "tree-folder", open: true }, h("summary", null, group.folder ? group.folder + "/" : label("repositoryRoot")), h("ul", null, items.map(function (fileState) { return fileState.item; })));
      return { node: node, items: items };
    });
    function filter() {
      var query = search.value.trim().toLowerCase();
      var shown = 0;
      groups.forEach(function (group) {
        var visible = 0;
        group.items.forEach(function (fileState) {
          var match = fileState.file.path.toLowerCase().indexOf(query) >= 0 && (!onlyNotes.checked || notes[fileState.file.id]);
          fileState.item.hidden = !match;
          fileState.section.hidden = !match;
          if (match) visible += 1;
        });
        group.node.hidden = !visible;
        shown += visible;
      });
      empty.hidden = shown > 0;
    }
    search.addEventListener("input", filter);
    onlyNotes.addEventListener("change", filter);
    searchBox = search;
    return h("aside", { class: "sidebar" },
      h("h2", null, label("changedFiles") + " ", h("span", { class: "muted" }, changes.stats.files)),
      search,
      analysis ? h("label", { class: "only-notes" }, onlyNotes, " " + label("onlyNotes")) : null,
      h("nav", { class: "tree" }, groups.map(function (group) { return group.node; }), empty));
  }

  // -- toolbar --------------------------------------------------------------------------------

  function redrawDiffs() {
    fileStates.forEach(function (fileState) { if (fileState.rendered) renderFile(fileState); });
  }
  function toggleButton(text, isOn, action, title) {
    var button = h("button", { type: "button", class: "tool", title: title, "aria-pressed": isOn() ? "true" : "false", onclick: function () {
      action();
      button.setAttribute("aria-pressed", isOn() ? "true" : "false");
    } }, text);
    return button;
  }
  function choice(text, isOn, action, title) {
    return h("button", { type: "button", class: "tool", title: title, "aria-pressed": isOn ? "true" : "false", onclick: action }, text);
  }
  function applyTheme() {
    if (state.theme) document.documentElement.setAttribute("data-theme", state.theme);
  }
  function setView(view) {
    state.view = view;
    remember("view", view);
    draw();
  }
  // The whole page is drawn again, because both its labels and the review text change.
  function setLanguage(language) {
    state.lang = language;
    remember("lang", language);
    draw();
  }
  function toolbar() {
    return h("header", { class: "topbar" },
      h("a", { class: "brand", href: "#summary" }, "Local PR Review"),
      h("span", { class: "topbar-branch", title: changes.scope.label }, changes.repo.branch),
      h("span", { class: "spacer" }),
      h("span", { class: "segmented", title: label("languageHint") },
        choice("VI", state.lang === "vi", function () { setLanguage("vi"); }, "Tiếng Việt"),
        choice("EN", state.lang === "en", function () { setLanguage("en"); }, "English")),
      h("span", { class: "segmented" },
        choice(label("unified"), state.view === "unified", function () { setView("unified"); }),
        choice(label("split"), state.view === "split", function () { setView("split"); })),
      toggleButton(label("wrap"), function () { return state.wrap; }, function () {
        state.wrap = !state.wrap;
        remember("wrap", state.wrap);
        document.body.classList.toggle("wrap", state.wrap);
      }, label("wrapHint")),
      analysis ? toggleButton(label("aiNotes"), function () { return state.notes; }, function () {
        state.notes = !state.notes;
        document.body.classList.toggle("hide-notes", !state.notes);
      }, label("aiNotesHint")) : null,
      h("button", { type: "button", class: "tool", title: label("foldAllHint"), onclick: function () {
        var collapse = fileStates.some(function (fileState) { return !fileState.collapsed; });
        fileStates.forEach(function (fileState) { setCollapsed(fileState, collapse); });
      } }, label("foldAll")),
      h("button", { type: "button", class: "tool", title: label("themeHint"), onclick: function () {
        var dark = state.theme ? state.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
        state.theme = dark ? "light" : "dark";
        remember("theme", state.theme);
        applyTheme();
      } }, label("theme")));
  }

  // -- drawing and start-up -------------------------------------------------------------------

  function draw() {
    var scroll = window.scrollY;
    if (observer) observer.disconnect();
    document.documentElement.setAttribute("lang", state.lang);
    document.title = (analysis ? plainText(analysis.title) : changes.repo.branch) + " · Local PR Review";

    fileStates = changes.files.map(fileSection);
    var side = sidebar();
    var totalLines = changes.files.reduce(function (sum, file) {
      return sum + file.segments.reduce(function (count, segment) { return count + segment.lines.length; }, 0);
    }, 0);
    // A big review draws each file when it scrolls into view, so the page opens fast.
    var lazy = totalLines > 6000 && !state.all && "IntersectionObserver" in window;
    observer = lazy ? new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        observer.unobserve(entry.target);
        var fileState = fileStates.filter(function (candidate) { return candidate.section === entry.target; })[0];
        if (fileState && !fileState.collapsed && !fileState.rendered) renderFile(fileState);
      });
    }, { rootMargin: "800px 0px" }) : null;

    fileStates.forEach(function (fileState) {
      var viewed = recall(fileState.viewedKey, false);
      var known = collapsedById[fileState.file.id];
      fileState.viewedBox.checked = viewed;
      fileState.section.classList.toggle("viewed", viewed);
      fileState.item.classList.toggle("viewed", viewed);
      if (known === undefined ? viewed || fileState.startsCollapsed : known) setCollapsed(fileState, true);
      else if (lazy) observer.observe(fileState.section);
      else renderFile(fileState);
    });

    var app = document.getElementById("app");
    while (app.firstChild) app.removeChild(app.firstChild);
    append(app, [toolbar(), h("div", { class: "layout" }, side, h("main", null, summary(),
      h("h2", { class: "section-title", id: "files" }, label("changes")),
      fileStates.map(function (fileState) { return fileState.section; })))]);
    document.body.classList.toggle("hide-notes", !state.notes);
    window.scrollTo(0, scroll);
  }

  function hashOptions() {
    var options = {};
    window.location.hash.replace(/^#/, "").split("&").forEach(function (pair) {
      var parts = pair.split("=");
      if (parts[0]) options[parts[0]] = parts[1] === undefined ? true : parts[1];
    });
    return options;
  }

  function keyboard() {
    function step(direction) {
      var visible = fileStates.filter(function (fileState) { return !fileState.section.hidden; });
      var current = -1;
      visible.forEach(function (fileState, number) { if (fileState.section.getBoundingClientRect().top <= 80) current = number; });
      var next = visible[Math.min(Math.max(current + direction, 0), visible.length - 1)];
      if (next) next.section.scrollIntoView({ block: "start" });
    }
    document.addEventListener("keydown", function (event) {
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
      if (event.key === "Escape" && typing) { event.target.blur(); return; }
      if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "/") { event.preventDefault(); searchBox.focus(); }
      else if (event.key === "j") step(1);
      else if (event.key === "k") step(-1);
    });
  }

  function start() {
    var options = hashOptions();
    function known(value, allowed, fallback) { return allowed.indexOf(value) >= 0 ? value : fallback; }
    state.view = known(options.view, ["unified", "split"], recall("view", "unified"));
    state.lang = known(options.lang, ["vi", "en"], known(recall("lang", null), ["vi", "en"], defaultLanguage(window.navigator.language)));
    state.wrap = recall("wrap", false);
    state.theme = recall("theme", null);
    state.all = Boolean(options.all);
    applyTheme();
    document.body.classList.toggle("wrap", state.wrap);
    draw();
    keyboard();
    window.addEventListener("beforeprint", function () {
      fileStates.forEach(function (fileState) { if (!fileState.collapsed && !fileState.rendered) renderFile(fileState); });
    });
    document.body.setAttribute("data-ready", "1");
  }

  start();
})();
