// STUB — filled in during the views build phase.
// Required exports (imported by app.js): listView, newView, detailView, leadView.
import { mount, el } from '../ui.js';
const stub = (name) => async () => mount(el(`<div class="empty">${name} — coming soon in this build.</div>`));
export const listView = stub('Projects');
export const newView = stub('New project');
export const detailView = stub('Project');
export const leadView = stub('Lead screen');
