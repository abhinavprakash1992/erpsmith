// Reference vectors from the Node server's own embed(), for the Python parity test.
// Usage: node dev/export-node-embeddings.mjs ~/Desktop/erpnext-ai-agent/mcp > erpsmith/tests/fixtures/node_embeddings.json
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const { embed } = await import(pathToFileURL(join(process.argv[2], "vectorMemory.js")).href);
const TEXTS = [
  "can I import json in a server script",
  "RULE 1 — NO import statements\n\nEvery import throws ImportError. frappe, json, datetime are already globals.",
  "Child table writes from server script don't work",
  "How do I create a Payment Entry from a Server Script?",
  "Ünïcödé — ₹50,000 e-way bill 🚚",
  "word ".repeat(1200), // > 512 tokens: exercises truncation
];
const rows = [];
for (const text of TEXTS) rows.push({ text, vector: await embed(text) });
console.log(JSON.stringify(rows));
