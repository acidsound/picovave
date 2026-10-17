/* SPDX-License-Identifier: GPL-3.0-only */
/* picovave stub: femto8's windowed GUI, replaced with no-ops.
 *
 * The device and the wasm page have no file browser, no modal dialogs and no
 * pause menu; the engine still links against their symbols. Errors are surfaced
 * by the port (a status line / the launcher), not by a drawn dialog.
 *
 * p8_dialog_* implement the API declared in p8_dialog.h; p8_show_pause_menu and
 * the p8_menuitem_* API are from p8_pause_menu.h. */
#include <stdbool.h>
#include <string.h>

#include "p8_dialog.h"
#include "p8_pause_menu.h"

bool m_dialog_showing = false;

p8_custom_menuitem_t m_custom_menuitems[MAX_CUSTOM_MENUITEMS];

void p8_dialog_init(p8_dialog_t *dialog, const char *title, p8_dialog_control_t *controls,
                    int count, int width)
{
    if (!dialog)
        return;
    dialog->title = title;
    dialog->controls = controls;
    dialog->control_count = count;
    dialog->width = width;
    dialog->height = 0;
    dialog->padding = 2;
    dialog->draw_border = true;
    dialog->x = -1;
    dialog->y = -1;
    dialog->focused_control = 0;
    dialog->focused_subcontrol = 0;
    dialog->cursor_blink = 0;
    dialog->input_mode = false;
}

void p8_dialog_draw(const p8_dialog_t *dialog) { (void)dialog; }
void p8_dialog_draw_stack(void) {}

p8_dialog_action_t p8_dialog_update(p8_dialog_t *dialog)
{
    (void)dialog;
    p8_dialog_action_t action;
    action.type = DIALOG_RESULT_NONE;
    action.action_id = 0;
    action.button_mask = 0;
    return action;
}

p8_dialog_action_t p8_dialog_run(p8_dialog_t *dialog)
{
    (void)dialog;
    p8_dialog_action_t action;
    action.type = DIALOG_RESULT_ACCEPTED;
    action.action_id = 0;
    action.button_mask = 0;
    return action;
}

void p8_dialog_set_showing(p8_dialog_t *dialog, bool showing)
{
    (void)dialog;
    m_dialog_showing = showing;
}

void p8_dialog_cleanup(p8_dialog_t *dialog) { (void)dialog; }

void p8_show_pause_menu(void) {}

void p8_menuitem_set(int index, const char *label, int lua_callback_ref)
{
    (void)index;
    (void)label;
    (void)lua_callback_ref;
}

void p8_menuitem_set_label(int index, const char *label) { (void)index; (void)label; }
void p8_menuitem_clear(int index) { (void)index; }
void p8_menuitem_reset_all(void) {}
