rm nyarch-repo*
rm *debug*

latest_mirrorlist=$(ls -v nyarch-mirrorlist-*.pkg.tar.zst | tail -1)
cp "$latest_mirrorlist" repo/nyarch-mirrorlist.pkg.tar.zst
cp "${latest_mirrorlist}.sig" repo/nyarch-mirrorlist.pkg.tar.zst.sig

latest_keyring=$(ls -v nyarch-keyring-*.pkg.tar.zst | tail -1)
cp "$latest_keyring" repo/nyarch-keyring.pkg.tar.zst
cp "${latest_keyring}.sig" repo/nyarch-keyring.pkg.tar.zst.sig

repo-add nyarch-repo.db.tar.gz *.pkg.tar.zst
