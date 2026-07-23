import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export default class NotificationPosition {
    enable() {
        this._bannerActor = Main.messageTray._bannerBin ?? Main.messageTray.actor ?? null;
        this._originalBannerAlignment = Main.messageTray.bannerAlignment;
        this._originalYAlign =
            (this._bannerActor && this._bannerActor.get_y_align)
                ? this._bannerActor.get_y_align()
                : Clutter.ActorAlign.START; // 默认为顶端
        Main.messageTray.bannerAlignment = Clutter.ActorAlign.END;
        if (this._bannerActor && this._bannerActor.set_y_align)
            this._bannerActor.set_y_align(Clutter.ActorAlign.END);
    }

    disable() {
        if (this._originalBannerAlignment === undefined)
            return;

        Main.messageTray.bannerAlignment = this._originalBannerAlignment;

        const bannerActor = Main.messageTray._bannerBin ?? Main.messageTray.actor ?? null;
        if (bannerActor && bannerActor.set_y_align)
            bannerActor.set_y_align(this._originalYAlign);

        this._bannerActor = null;
        this._originalBannerAlignment = undefined;
        this._originalYAlign = undefined;
    }
}
