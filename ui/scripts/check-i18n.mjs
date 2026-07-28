#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--json") {
      args.json = true;
      continue;
    }
    if (arg.startsWith("--") && arg.includes("=")) {
      const [rawKey, rawValue] = arg.split("=", 2);
      args[rawKey.slice(2)] = rawValue;
      continue;
    }
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const next = argv[i + 1];
      if (!next || next.startsWith("--")) {
        args[key] = true;
      } else {
        args[key] = next;
        i += 1;
      }
    }
  }
  return args;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

function collectFiles(rootDir) {
  const result = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === "dist" || entry.name === ".git") {
        continue;
      }
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile()) {
        const ext = path.extname(full);
        if (ext === ".ts" || ext === ".tsx") {
          result.push(full);
        }
      }
    }
  };
  walk(rootDir);
  return result;
}

function isTargetAttribute(name) {
  return ["placeholder", "title", "alt", "aria-label", "ariaLabel"].includes(name);
}

function collectLeafKeys(data, prefix = "") {
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const keys = new Set();
    for (const [key, value] of Object.entries(data)) {
      const next = prefix ? `${prefix}.${key}` : key;
      for (const child of collectLeafKeys(value, next)) {
        keys.add(child);
      }
    }
    return keys;
  }
  return new Set([prefix]);
}

function isStaticText(expr) {
  return ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr);
}

function recordNodeText(node, state, filePath, kind) {
  const { sourceFile, findings } = state;
  if (!node) {
    return;
  }
  const text = String(node.text || "").trim();
  if (!text || !/[\p{L}\p{N}]/u.test(text)) {
    return;
  }
  let line = 1;
  let column = 1;
  if (typeof node.getStart === "function") {
    const loc = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    line = loc.line + 1;
    column = loc.character + 1;
  } else if (typeof node.pos === "number") {
    const loc = sourceFile.getLineAndCharacterOfPosition(node.pos);
    line = loc.line + 1;
    column = loc.character + 1;
  }
  findings.push({
    file: filePath,
    line,
    column,
    type: kind,
    value: text,
  });
}

function collectVisible(node, state, filePath) {
  if (!node) {
    return;
  }
  const { sourceFile } = state;

  if (ts.isJsxAttribute(node)) {
    const name = node.name?.getText(sourceFile);
    if (!isTargetAttribute(name) || !node.initializer) {
      return;
    }
    const expr = ts.isJsxExpression(node.initializer) ? node.initializer.expression : node.initializer;
    if (isStaticText(expr)) {
      recordNodeText(expr, state, filePath, `jsx-attr:${name}`);
    }
    return;
  }

  if (ts.isJsxText(node)) {
    const raw = node
      .getText(sourceFile)
      .replace(/\r/g, "")
      .trim();
    if (!raw) {
      return;
    }
    recordNodeText({ text: raw, pos: node.getStart(sourceFile) }, state, filePath, "jsx-text");
    return;
  }

  if (ts.isJsxExpression(node)) {
    const expr = node.expression;
    if (!expr) {
      return;
    }
    if (ts.isConditionalExpression(expr)) {
      collectStringExpression(expr.whenTrue, state, filePath);
      collectStringExpression(expr.whenFalse, state, filePath);
      return;
    }
    if (ts.isBinaryExpression(expr)) {
      if (
        expr.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken ||
        expr.operatorToken.kind === ts.SyntaxKind.BarBarToken
      ) {
        collectStringExpression(expr.right, state, filePath);
        return;
      }
    }
    collectStringExpression(expr, state, filePath);
  }
}

function collectStringExpression(expr, state, filePath) {
  if (!expr) {
    return;
  }
  if (isStaticText(expr)) {
    recordNodeText(expr, state, filePath, "jsx-string-expression");
    return;
  }
  if (ts.isTemplateExpression(expr) && expr.templateSpans.length === 0) {
    recordNodeText(expr.head, state, filePath, "jsx-template-expression");
  }
}

function extractDynamicFamily(expression) {
  while (
    ts.isAsExpression(expression) ||
    ts.isTypeAssertionExpression(expression) ||
    ts.isParenthesizedExpression(expression) ||
    ts.isSatisfiesExpression(expression)
  ) {
    expression = expression.expression;
  }
  if (!ts.isTemplateExpression(expression) || expression.templateSpans.length !== 1) {
    return null;
  }
  if (expression.templateSpans[0].literal.text !== "") {
    return null;
  }
  const prefix = expression.head.text;
  if (prefix.endsWith(".") && prefix.length > 1) {
    return prefix.slice(0, -1);
  }
  return null;
}

function collectFromNode(node, state, filePath) {
  const { sourceFile, usedLiteralKeys, usedFamilies, unknownKeys, enLeafKeys, allowlist } = state;

  if (ts.isJsxAttribute(node)) {
    const name = node.name?.getText(sourceFile);
    if (!isTargetAttribute(name)) {
      return;
    }
  }

  if (ts.isCallExpression(node)) {
    const callee = node.expression;
    if (ts.isIdentifier(callee) && callee.text === "t" && node.arguments.length >= 1) {
      const arg = node.arguments[0];
      if (isStaticText(arg)) {
        const key = String(arg.text);
        if (enLeafKeys.has(key)) {
          usedLiteralKeys.add(key);
        } else {
          const loc = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
          unknownKeys.push({
            file: filePath,
            line: loc.line + 1,
            column: loc.character + 1,
            key,
            type: "unknown-t-key",
          });
        }
        return;
      }
      const family = extractDynamicFamily(arg);
      const familyKeys = family
        ? [...enLeafKeys].filter((key) => key.startsWith(`${family}.`))
        : [];
      const isBuiltInFamily = family === "nav" || family === "ws";
      const isAllowlistedFamily =
        familyKeys.length > 0 && familyKeys.every((key) => isUnusedAllowed(key, allowlist));
      if (family && (isBuiltInFamily || isAllowlistedFamily)) {
        usedFamilies.add(family);
        return;
      }
      const argumentText = arg ? arg.getText() : "<empty>";
      if (isUnusedAllowed(argumentText, allowlist)) {
        return;
      }
      const loc = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
      unknownKeys.push({
        file: filePath,
        line: loc.line + 1,
        column: loc.character + 1,
        key: argumentText,
        type: "nonliteral-t-key",
      });
      return;
    }
  }

  collectVisible(node, state, filePath);
  ts.forEachChild(node, (child) => collectFromNode(child, state, filePath));
}

function readAllowlist(filePath) {
  if (!fs.existsSync(filePath)) {
    return [];
  }
  const raw = readJson(filePath);
  if (!Array.isArray(raw?.allowlist)) {
    return [];
  }
  return raw.allowlist;
}

function isUnusedAllowed(key, allowlist) {
  for (const entry of allowlist) {
    if (entry?.type === "key" && entry.key === key) {
      return true;
    }
    if (entry?.type === "key-pattern" && typeof entry.pattern === "string") {
      try {
        if (new RegExp(entry.pattern).test(key)) {
          return true;
        }
      } catch {
        // ignore invalid regex
      }
    }
  }
  return false;
}

function detectDefaultPaths(rootPath, relativeFile) {
  if (path.isAbsolute(relativeFile)) {
    return relativeFile;
  }

  const direct = path.resolve(rootPath, relativeFile);
  if (fs.existsSync(direct)) {
    return direct;
  }

  const trimmed = relativeFile.startsWith("ui/") ? relativeFile.slice(3) : null;
  if (trimmed) {
    const trimmedPath = path.resolve(rootPath, trimmed);
    if (fs.existsSync(trimmedPath)) {
      return trimmedPath;
    }
  }

  const uiRoot = path.resolve(rootPath, "ui", relativeFile);
  if (fs.existsSync(uiRoot)) {
    return uiRoot;
  }

  const altPath = path.resolve(process.cwd(), relativeFile);
  if (fs.existsSync(altPath) && !path.isAbsolute(rootPath)) {
    return altPath;
  }

  return direct;
}

function scanDirectory(srcDir, enLeafKeys, allowlistFile) {
  const files = collectFiles(srcDir);
  const allowlist = readAllowlist(allowlistFile);
  const findings = [];
  const unknownKeys = [];
  const usedLiteralKeys = new Set();
  const usedFamilies = new Set();

  for (const filePath of files) {
    const source = fs.readFileSync(filePath, "utf-8");
    const kind = path.extname(filePath) === ".tsx" ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sourceFile = ts.createSourceFile(filePath, source, ts.ScriptTarget.ES2022, true, kind);
    const state = {
      sourceFile,
      findings,
      unknownKeys,
      usedLiteralKeys,
      usedFamilies,
      enLeafKeys,
      allowlist,
    };
    ts.forEachChild(sourceFile, (node) => collectFromNode(node, state, filePath));
  }

  const allUsed = new Set(usedLiteralKeys);
  for (const family of usedFamilies) {
    for (const key of enLeafKeys) {
      if (key.startsWith(`${family}.`)) {
        allUsed.add(key);
      }
    }
  }

  const unusedKeys = [...enLeafKeys].filter((key) => !allUsed.has(key) && !isUnusedAllowed(key, allowlist)).sort();

  return {
    hardcodedText: findings,
    unknownKeys,
    unusedKeys,
    allowlist,
    sourceFiles: files.length,
  };
}

function subsetCheck(en, zh) {
  const enKeys = collectLeafKeys(en);
  const zhKeys = collectLeafKeys(zh);
  return {
    missing: [...enKeys].filter((key) => !zhKeys.has(key)).sort(),
    extra: [...zhKeys].filter((key) => !enKeys.has(key)).sort(),
  };
}

function main() {
  const args = parseArgs(process.argv);
  const cwd = process.cwd();
  const root = path.resolve(cwd, args.root || "");

  const explicitSrcDir = args["src-dir"] ? path.resolve(root, args["src-dir"]) : null;
  let srcDir = explicitSrcDir;
  if (!srcDir) {
    srcDir = fs.existsSync(path.resolve(root, "ui/src")) ? path.resolve(root, "ui/src") : path.resolve(root, "src");
  }

  if (!fs.existsSync(srcDir)) {
    console.error(`Source directory not found: ${srcDir}`);
    process.exit(1);
  }

  const enPath = detectDefaultPaths(root, args.en ? args.en : path.join("src", "i18n", "locales", "en.json"));
  const zhPath = detectDefaultPaths(root, args.zh ? args.zh : path.join("src", "i18n", "locales", "zh-CN.json"));
  const allowlistPath = detectDefaultPaths(root, args.allowlist ? args.allowlist : path.join("ui", "scripts", "i18n-allowlist.json"));

  if (!fs.existsSync(enPath) || !fs.existsSync(zhPath)) {
    console.error(`Missing locale files: ${enPath} / ${zhPath}`);
    process.exit(1);
  }

  const en = readJson(enPath);
  const zh = readJson(zhPath);
  const enLeafKeys = collectLeafKeys(en);

  const scan = scanDirectory(srcDir, enLeafKeys, allowlistPath);
  const subset = subsetCheck(en, zh);

  const payload = {
    files: {
      en: enPath,
      zh: zhPath,
      allowlist: allowlistPath,
    },
    summary: {
      sourceDirectory: srcDir,
      sourceFiles: scan.sourceFiles,
      hardcodedTextCount: scan.hardcodedText.length,
      unknownTKeyCount: scan.unknownKeys.length,
      unusedKeyCount: scan.unusedKeys.length,
      zhMissingCount: subset.missing.length,
      zhExtraCount: subset.extra.length,
    },
    issues: {
      hardcodedText: scan.hardcodedText,
      unknownKeys: scan.unknownKeys,
      unusedKeys: scan.unusedKeys,
      allowlist: scan.allowlist,
    },
  };

  const isRed = payload.summary.hardcodedTextCount > 0 || payload.summary.unknownTKeyCount > 0 || payload.summary.unusedKeyCount > 0 || payload.summary.zhExtraCount > 0;
  payload.ok = !isRed;
  payload.status = payload.ok ? "GREEN" : "RED";

  if (args.json) {
    console.log(JSON.stringify(payload, null, 2));
  } else {
    console.log(`[i18n] status=${payload.status}`);
    console.log(`sourceFiles=${payload.summary.sourceFiles}`);
    console.log(`hardcodedText=${payload.summary.hardcodedTextCount}`);
    console.log(`unknownTKeys=${payload.summary.unknownTKeyCount}`);
    console.log(`unusedKeys=${payload.summary.unusedKeyCount}`);
  }

  process.exit(payload.ok ? 0 : 1);
}

main();
