# Implementation Learnings — 009 Wrapper Task (Repeat Group)

## 1. SortableJS nested groups don't work inside sortable items

**Assumption:** SortableJS with `group: { name: 'shared', put: true }` on a nested container (loop drop zone) inside a parent sortable item would allow cross-container drops.

**Reality:** When the nested container lives inside an `<li>` that the parent Sortable considers draggable, the parent intercepts all drop events. The nested Sortable never receives the drop. This is a fundamental architectural limitation of SortableJS.

**Fix:** Replaced SortableJS with **dragula** (3.7.3, ~14KB). Dragula explicitly defines multiple containers as peers — the main queue list and each loop's drop zone are all registered equally. No parent/child conflict.

**Takeaway:** For cross-container DnD where drop targets are nested inside draggable items of a parent list, use a library that treats containers as peers (dragula) rather than one that relies on nested sortable hierarchies (SortableJS).

## 2. CSS `touch-action` must be set before touch starts

**Assumption:** Setting `document.body.style.touchAction = 'none'` in dragula's `drag` event callback would prevent mobile scrolling during drag.

**Reality:** By the time the `drag` event fires, the browser has already committed to a scroll gesture from the initial `touchstart`. Dynamic style changes during drag are too late.

**Fix:** Added `touch-action: none` as a static CSS property on `.queue-item` elements. The browser reads this before the touch begins, so it never starts scrolling when touching a queue item.

**Takeaway:** `touch-action` is a pre-touch declaration, not a runtime toggle. Set it in CSS on elements that should be draggable on mobile.

## 3. CSS `transform: rotate()` rotates the entire element including text

**Assumption:** Using `transform: rotate(90deg)` on an expand button (previously just `▸`) would still work after adding text content ("▸ Details").

**Reality:** The rotation applied to the entire button element, making "▸ Details" render sideways — which looked broken.

**Fix:** Removed the rotation entirely. Instead, swap the text content between `▸ Details` and `▾ Details` via JavaScript in `toggleExpand()`.

**Takeaway:** Don't use CSS transforms for state indication on elements with readable text. Use content swaps instead.

## 4. Nested font-size compounds when using relative units

**Assumption:** Loop children (`.loop-child-item` inside `.loop-children` inside a `.queue-item`) would inherit the same `0.85rem` font size as regular queue items.

**Reality:** The `.loop-children` container previously had `font-size: 0.8rem`. Even after removing it, the child `.queue-item` class set `0.85rem` — but since it was nested inside a parent already at `0.85rem`, the effective size was smaller than top-level items (rem is relative to root, so this specific case was fine, but the earlier `0.8rem` on the container did cause visible difference).

**Fix:** Explicitly set `font-size: 0.85rem` on `.loop-child-item` and removed all intermediate font-size overrides from `.loop-children`.

**Takeaway:** When nesting styled components, explicitly set font-size on the innermost element rather than relying on inheritance through intermediate containers that may have their own overrides.

## 5. Emoji icons render inconsistently on mobile

**Assumption:** Emoji (🗑, ⏹, 🔁) would render consistently across desktop and mobile browsers as button labels.

**Reality:** On mobile devices, emoji sizing and rendering varies significantly. Some appear oversized, misaligned, or broken depending on the OS emoji font.

**Fix:** Replaced all emoji with plain text labels ("Delete", "Stop", "Loop"). Text renders consistently everywhere and communicates intent more clearly.

**Takeaway:** For UI controls, prefer text labels over emoji. Emoji are fine for decorative/informational display but unreliable as functional icons, especially on mobile.

## 6. Dragula `moves` callback receives the clicked element, not the drag handle

**Assumption:** Dragula's `moves(el, source, handle)` parameter `handle` would be the `.drag-handle` element.

**Reality:** `handle` is the actual DOM element that received the mousedown/touchstart event — which could be any child element inside the item (a span, button, text node wrapper, etc.).

**Fix:** Walk up the DOM tree from `handle` to check if any ancestor (up to `el`) has the `.drag-handle` class. Later simplified to: allow drag from anywhere except buttons/inputs (walk up checking for BUTTON/INPUT/LABEL tags).

**Takeaway:** Dragula's `handle` parameter is the event target, not a pre-filtered handle element. Always walk up the DOM tree or use tag-based exclusions.

## 7. Piecemeal loop-child APIs lose drop position

**Assumption:** Backing drag-and-drop on loops with three small endpoints — `add_child_to_loop`, `remove_child_from_loop`, and the flat `reorder(ids)` for the main queue — was enough, because dragula moves the DOM to the exact drop position and the WebSocket re-render would reflect it.

**Reality:** Every one of those endpoints threw the position away:
- `add_child_to_loop` always **appended** to `loop.children`, so dropping a task between two loop children put it at the end.
- `remove_child_from_loop` on a "drag out of loop" **deleted** the task instead of moving it back to the pending queue at the drop position.
- There was no endpoint at all for reordering *within* a loop's children, so intra-loop drags visually snapped back on the next re-render.

From the user's POV: adding, removing, or reordering anything near a loop looked like the whole loop got "reshuffled" — because the backend order didn't match what dragula had just rendered.

**Fix:** Replaced the three endpoints (for drag paths) with a single hierarchical reorder. Added `TaskQueue.set_order(items)` where `items = [{"id": str, "children": [ids]?}, ...]`. It validates (every current id present exactly once, `children` only under loops, no nested loops) and atomically rebuilds the deque and each loop's `children` list. `POST /api/queue/reorder` now accepts either `ids` (legacy flat) or `items` (hierarchical). On every dragula `drop`, the client snapshots the entire DOM hierarchy (top-level items + each `.loop-drop-zone`'s current children, matching both `.loop-child-item` and the just-moved `.queue-item` that hasn't been re-rendered yet) and submits it in one call.

**Takeaway:** For hierarchical drag-and-drop, don't split "move within", "move in", and "move out" into separate operations that each `append`/`delete`. Model the drop result as a single "here's the new tree, apply it atomically" request and rebuild from the DOM. It's simpler, race-free, and preserves the exact position the user chose. As a bonus, drag-out-of-loop stopped deleting tasks.
