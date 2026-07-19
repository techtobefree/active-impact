// STUB — filled in during the views build phase.
// Required exports (imported by app.js): listView, newView, detailView.
import { mount, el } from '../ui.js';
const stub = (name) => async () => mount(el(`<div class="empty">${name} — coming soon in this build.</div>`));
export const listView = stub('Catalog');
export const newView = stub('New listing');
export const detailView = stub('Listing');
