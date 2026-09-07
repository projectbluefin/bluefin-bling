// SPDX-FileCopyrightText: 2023 Florian Müllner <fmuellner@gnome.org>
// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class LightStyleExtension extends Extension {
    enable() {
        this._interfaceSettings = new Gio.Settings({
            schema_id: 'org.gnome.desktop.interface',
        });

        this._colorSchemeId = this._interfaceSettings.connect(
            'changed::color-scheme',
            () => this._sync()
        );

        this._sync();
    }

    _sync() {
        const scheme = this._interfaceSettings.get_string('color-scheme');
        const isDark = scheme === 'prefer-dark';

        if (isDark) {
            Main.uiGroup.remove_style_class_name('light-style-active');
            Main.sessionMode.colorScheme = 'prefer-dark';
        } else {
            Main.uiGroup.add_style_class_name('light-style-active');
            Main.sessionMode.colorScheme = 'prefer-light';
        }

        St.Settings.get().notify('color-scheme');
    }

    disable() {
        if (this._colorSchemeId) {
            this._interfaceSettings.disconnect(this._colorSchemeId);
            this._colorSchemeId = null;
        }
        this._interfaceSettings = null;

        Main.uiGroup.remove_style_class_name('light-style-active');
        Main.sessionMode.colorScheme = 'prefer-dark';
        St.Settings.get().notify('color-scheme');
    }
}
