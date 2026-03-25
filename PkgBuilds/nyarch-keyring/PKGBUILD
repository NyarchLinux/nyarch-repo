# maintainter: Hunter Tadlock-Barnes
# used Arch Linux CN ( http://www.archlinuxcn.org ). as format
# See COPYING for license details.
# pkgver is YYYYMMDD
pkgname=nyarch-keyring #done
pkgver=2
pkgrel=1
pkgdesc='Nyarch GPG keyring'
arch=('any')
url='https://https://github.com/TotallyDIO/NyarchKeyringPkgbuild'
license=('GPL')
depends=('archlinux-keyring')
makedepends=('git')
optdepends=('pkgstats: install to submit package usage statistics')
install="nyarch-keyring.install"
source=("git+https://github.com/TotallyDIO/NyarchKeyring.git#tag=${pkgver}")
sha512sums=('SKIP')

package() {
  cd NyarchKeyring
  make PREFIX=/usr DESTDIR="$pkgdir" install
}
