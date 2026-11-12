// Rasterize the ReportLab scientific SVGs with an already installed local sharp.
const fs = require('node:fs');
const path = require('node:path');
const args = process.argv.slice(2);
const outDir = path.resolve(args[0] || '.');
const sharp = require(args[1] || 'sharp');
(async () => {
  for (const name of ['summary', 'frontier', 'latency-cdf']) {
    const output = path.join(outDir, `${name}.png`);
    if (fs.existsSync(output)) throw new Error(`Refusing to overwrite ${output}`);
    await sharp(path.join(outDir, `${name}.svg`), { density: 150 }).png().toFile(output);
  }
  process.stdout.write(JSON.stringify({ directory: outDir, pngCount: 3 }) + '\n');
})().catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
