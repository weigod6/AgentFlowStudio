import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const requested = process.argv.slice(2);
const roots = requested.length ? requested : ["experiments/episode-loop-phase2/common"];

function jsFiles(root) {
  if (!existsSync(root)) throw new Error(`Prototype JS root does not exist: ${root}`);
  const stat = statSync(root);
  if (!stat.isDirectory()) return root.endsWith(".js") || root.endsWith(".mjs") ? [root] : [];
  return readdirSync(root).flatMap((name) => jsFiles(join(root, name)));
}

const files = roots.flatMap(jsFiles).sort();
if (!files.length) throw new Error("No prototype JavaScript files found.");

let failed = false;
for (const file of files) {
  const result = spawnSync(process.execPath, ["--check", file], {
    encoding: "utf8",
    shell: false,
  });
  if (result.status === 0) continue;
  failed = true;
  console.error(`JS syntax check failed: ${relative(process.cwd(), file)}`);
  if (result.stderr) console.error(result.stderr.trim());
  if (result.stdout) console.error(result.stdout.trim());
}

if (failed) process.exit(1);
console.log(`Phase 2 prototype JS syntax check passed: ${files.length} files`);
