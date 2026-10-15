import frappe
from frappe.model.document import Document

from erpsmith import memory, vector_cache, vectors


class MCPMemory(Document):
	def before_naming(self):
		# set_new_name() clears the name right after this hook; keep one that seed or import code chose on purpose
		self.flags.preset_name = self.name

	def autoname(self):
		self.name = self.flags.preset_name or memory.make_id(self.topic, self.title)

	def validate(self):
		self.title = (self.title or "").strip()
		self.content = (self.content or "").strip()
		if not self.title or not self.content:
			frappe.throw("A memory note needs both a title and content.", frappe.ValidationError)
		if len(self.title) > memory.MAX_TITLE_CHARS:
			frappe.throw(f"The title has {len(self.title)} characters; the limit is {memory.MAX_TITLE_CHARS}.", frappe.ValidationError)
		if len(self.content) > memory.MAX_CONTENT_CHARS:
			frappe.throw(f"The content has {len(self.content)} characters; the limit is {memory.MAX_CONTENT_CHARS}.", frappe.ValidationError)

	def before_save(self):
		# validate() has already refused anything too long, so embed() never sees a huge string
		if not self.vector or self.has_value_changed("title") or self.has_value_changed("content"):
			self.vector = vectors.to_b64(vectors.embed(memory.note_text(self.title, self.content)))

	def on_update(self):
		if not self.flags.skip_invalidate:
			vector_cache.invalidate(memory.DOCTYPE)

	def on_trash(self):
		if not self.flags.skip_invalidate:
			vector_cache.invalidate(memory.DOCTYPE)
