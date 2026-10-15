from frappe.model.document import Document

from erpsmith import vector_cache

DOCTYPE = "MCP Knowledge Chunk"


class MCPKnowledgeChunk(Document):
	def on_update(self):
		if not self.flags.skip_invalidate:
			vector_cache.invalidate(DOCTYPE)

	def on_trash(self):
		if not self.flags.skip_invalidate:
			vector_cache.invalidate(DOCTYPE)
