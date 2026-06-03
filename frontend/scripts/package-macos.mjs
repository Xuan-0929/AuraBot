import { cpSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const appName = "AuraBot";
const bundleId = "com.xuan.aurabot";
const distDir = join(rootDir, "dist");
const appBundlePath = join(distDir, `${appName}.app`);
const electronBundlePath = join(rootDir, "node_modules", "electron", "dist", "Electron.app");
const electronVersionPath = join(rootDir, "node_modules", "electron", "dist", "version");
const appResourcePath = join(appBundlePath, "Contents", "Resources", "app");
const plistPath = join(appBundlePath, "Contents", "Info.plist");
const electronVersion = readFileSync(electronVersionPath, "utf8").trim();

run("npm", ["run", "build"]);

rmSync(appBundlePath, { force: true, recursive: true });
mkdirSync(distDir, { recursive: true });
cpSync(electronBundlePath, appBundlePath, { recursive: true });

setPlistValue("CFBundleDisplayName", appName);
setPlistValue("CFBundleName", appName);
setPlistValue("CFBundleIdentifier", bundleId);
setPlistValue("CFBundleShortVersionString", readPackageVersion());
setPlistValue("CFBundleVersion", readPackageVersion());
setPlistValue("LSApplicationCategoryType", "public.app-category.productivity");

rmSync(appResourcePath, { force: true, recursive: true });
mkdirSync(join(appBundlePath, "Contents", "Resources"), { recursive: true });

cpSync(join(rootDir, "out"), join(appResourcePath, "out"), { recursive: true });
cpSync(join(rootDir, "package.json"), join(appResourcePath, "package.json"));
cpSync(join(rootDir, "node_modules"), join(appResourcePath, "node_modules"), { recursive: true });

removeDevelopmentFiles(appResourcePath);

run(join(rootDir, "node_modules", ".bin", "electron-rebuild"), [
  "--version",
  electronVersion,
  "--module-dir",
  appResourcePath,
  "--force",
  "--which-module",
  "better-sqlite3"
]);

console.log(`Packaged ${appName} at ${appBundlePath}`);

function readPackageVersion() {
  const pkg = JSON.parse(readFileSync(join(rootDir, "package.json"), "utf8"));

  return pkg.version ?? "0.1.0";
}

function removeDevelopmentFiles(targetDir) {
  for (const entry of [
    "src",
    "tests",
    "scripts",
    "configs",
    "electron.vite.config.ts",
    "tsconfig.json",
    "README.md"
  ]) {
    rmSync(join(targetDir, entry), { force: true, recursive: true });
  }
}

function setPlistValue(key, value) {
  run("/usr/libexec/PlistBuddy", ["-c", `Set :${key} ${value}`, plistPath]);
}

function run(command, args) {
  console.log(`$ ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: rootDir,
    encoding: "utf8",
    stdio: "inherit"
  });

  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with exit code ${result.status}`);
  }
}
