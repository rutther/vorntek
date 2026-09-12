import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const packageRoot = join(frontendRoot, "node_modules", "@tabler", "core");
const outputRoot = resolve(
  frontendRoot,
  "..",
  "console",
  "static",
  "console",
  "vendor",
  "tabler",
  "1.4.0",
);

const files = [
  [join(packageRoot, "dist", "css", "tabler.min.css"), "tabler.min.css"],
  [join(packageRoot, "dist", "js", "tabler.min.js"), "tabler.min.js"],
  [join(packageRoot, "package.json"), "PACKAGE.json"],
];

const license = `The MIT License (MIT)

Copyright (c) 2018-2025 The Tabler Authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
`;

await mkdir(outputRoot, { recursive: true });
for (const [source, outputName] of files) {
  await copyFile(source, join(outputRoot, outputName));
}
await writeFile(join(outputRoot, "LICENSE"), license, "utf8");

const checksumTargets = [...files.map(([, outputName]) => outputName), "LICENSE"];
const checksumLines = [];
for (const outputName of checksumTargets) {
  const bytes = await readFile(join(outputRoot, outputName));
  const digest = createHash("sha256").update(bytes).digest("hex");
  checksumLines.push(`${digest}  ${outputName}`);
}
await writeFile(
  join(outputRoot, "SHA256SUMS"),
  `${checksumLines.join("\n")}\n`,
  "utf8",
);

process.stdout.write(`Vendored @tabler/core 1.4.0 to ${outputRoot}\n`);
