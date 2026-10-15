// Dumps the Node server's memory DB and its answers to the parity queries, for the Python port's tests.
// Usage: node dev/export-node-memory.mjs <path to erpnext-ai-agent/mcp> dev/parity-queries.json private
// Writes <out dir>/node-memory.json (every note: id, topic, title, content, created_at)
//    and <out dir>/node-reference-top3.json (the Node server's top 3 per query, and the score gap between its #3 and #4).
// It reads the Node server's local database. The output is customer data: keep it in the git-ignored private/ folder.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const [mcpDir, queriesFile, outDir] = process.argv.slice(2);
if (!mcpDir || !queriesFile || !outDir) {
  console.error("usage: node dev/export-node-memory.mjs <mcp dir> <queries.json> <out dir>");
  process.exit(2);
}

const { searchMemory } = await import(pathToFileURL(join(mcpDir, "vectorMemory.js")).href);
const { connect } = createRequire(join(mcpDir, "package.json"))("@lancedb/lancedb");

const table = await (await connect(join(mcpDir, "..", "data", "memory_vectors"))).openTable("memory");
const notes = (await table.query().limit(100000).toArray())
  .map(({ id, topic, title, content, created_at }) => ({ id, topic, title, content, created_at }))
  .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));

const reference = [];
for (const query of JSON.parse(readFileSync(queriesFile, "utf8"))) {
  const { results } = await searchMemory({ query, topK: 4 });
  const margin = results.length === 4 ? Number((results[2].score - results[3].score).toFixed(4)) : null;
  reference.push({ query, top3: results.slice(0, 3).map(r => ({ id: r.id, score: r.score })), margin });
  if (margin === null || margin < 0.02) console.warn(`tie-prone (margin ${margin}): ${query}`);
}

mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "node-memory.json"), JSON.stringify(notes, null, 1));
writeFileSync(join(outDir, "node-reference-top3.json"), JSON.stringify(reference, null, 1));
console.log(`${notes.length} notes, ${reference.length} reference queries -> ${outDir}`);
