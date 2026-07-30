# Rental Watcher 🏠

Hollanda'daki kiralık ev sitelerini **10 dakikada bir GitHub Actions üzerinde (bulutta)**
kontrol eder, **yeni çıkan ilanları Telegram'a** gönderir. Bilgisayarınızın açık olmasına
gerek yoktur.

İzlenen siteler: Klik voor Kamers, Plaza (newnewnew.space), Magis Real Estate,
Holland2Stay (Tilburg + tümü), SSH (site + booking portalı), Pararius (Tilburg studio),
Kamernet (Tilburg), Funda (Tilburg furnished <1500€), Huurportaal (Tilburg).

## Nasıl çalışır?

- GitHub Actions her 10 dakikada bir `watcher.py --once` çalıştırır
  ([.github/workflows/watch.yml](.github/workflows/watch.yml)).
- Klik voor Kamers ve Plaza için sitelerin kendi JSON API'si kullanılır; diğer siteler önce
  düz HTTP ile denenir, ilan bulunamazsa headless Chromium (Playwright) ile render edilir.
- Görülen ilanlar `seen.json` dosyasında tutulur ve her kontrol sonrası repoya commit
  edilir; sadece **yeni** ilanlar Telegram'a bildirilir.
- **İlk çalıştırmada** mevcut ilanlar sessizce kaydedilir (yüzlerce mesaj gelmesin diye).
- Bir site arka arkaya 3 kontrol okunamazsa tek seferlik ⚠️ uyarısı, düzelince ✅ mesajı gelir.

## Kurulum

### 1. Telegram botu oluşturun

1. Telegram'da **@BotFather**'a yazın → `/newbot` → bota bir isim verin.
   BotFather `123456789:AAxx...` biçiminde bir **token** verir.
2. Oluşturduğunuz bota Telegram'dan herhangi bir mesaj atın (ör. "merhaba") —
   bot size ilk mesajı atamaz, önce siz yazmalısınız.
3. **Chat ID**'nizi öğrenin (TOKEN yazan yeri kendi token'ınızla değiştirin):

```bash
curl -s "https://api.telegram.org/botTOKEN/getUpdates" | python3 -c "import json,sys; [print(u['message']['chat']['id'], '-', u['message']['chat'].get('first_name','')) for u in json.load(sys.stdin)['result'] if 'message' in u]"
```

### 2. Bu klasörü GitHub'a yükleyin

Repo **Public** olmalı ya da GitHub ücretli planınız olmalı: Public repolarda Actions
dakikaları **sınırsız ve ücretsizdir**; Private repoların ücretsiz 2000 dk/ay limiti bu
sistem için yetmez (sistem ayda ~5000-8000 dk kullanır). Kodda gizli bilgi yoktur —
token'lar repoya değil, GitHub Secrets'a konur.

**Yol A — GitHub Desktop (kolay):**
1. [GitHub Desktop](https://desktop.github.com) uygulamasını kurun, GitHub hesabınızla giriş yapın.
2. *File → Add Local Repository* → bu klasörü (`rental-watcher`) seçin.
3. Değişiklikleri commit edin, sonra *Publish repository* → **"Keep this code private" işaretini KALDIRIN** → Publish.

**Yol B — Terminal:**
1. [github.com/new](https://github.com/new) adresinde `rental-watcher` adında **Public** bir repo
   oluşturun (README eklemeyin).
2. Push için bir [Personal Access Token](https://github.com/settings/tokens) oluşturun
   (repo yetkisiyle); şifre sorulduğunda bu token'ı girin:

```bash
cd "/Users/yilmazsahin/Desktop/My Zone/rental-watcher" && git remote add origin https://github.com/KULLANICI_ADINIZ/rental-watcher.git && git push -u origin main
```

### 3. Telegram bilgilerini GitHub Secrets'a ekleyin

Repo sayfasında: **Settings → Secrets and variables → Actions → New repository secret**

| İsim | Değer |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
| `TELEGRAM_CHAT_ID` | 1. adımda öğrendiğiniz sayı |

### 4. Çalıştırın

1. Repo sayfasında **Actions** sekmesine gidin; ilk kez soruyorsa workflow'ları etkinleştirin
   ("I understand my workflows, go ahead and enable them").
2. Soldan **Rental Watcher** → sağda **Run workflow** → Run workflow (elle ilk test).
3. Yeşil ✓ görürseniz sistem hazırdır — artık her 10 dakikada bir kendiliğinden çalışır.

## Önemli notlar

- **Zamanlama:** GitHub cron'u dakikası dakikasına garanti etmez; yoğun saatlerde kontroller
  3-10 dk gecikebilir. Pratikte 10-15 dk'da bir kontrol gerçekleşir.
- **Funda ve Pararius** güçlü bot koruması kullanır; GitHub'ın veri merkezi IP'lerini
  engelleyebilirler. Bu olursa o site için ⚠️ mesajı alırsınız, diğer siteler etkilenmez.
  Garanti olsun isterseniz Funda'da hesap açıp kendi "saved search" e-posta bildirimini de kurun.
- **Holland2Stay** şu an tüm otomatik erişimi Cloudflare doğrulamasıyla engelliyor (API dahil).
  Sistem her turda denemeye devam eder; kalıcı engelde ⚠️ mesajı alırsınız. H2S için en sağlamı
  sitede hesap açıp kendi bildirimlerini de açmaktır.
- **SSH (sshxl.nl)** ilanları üye girişi olmadan göstermiyor; bu yüzden ana sayfalardan
  bildirim gelmesi düşük ihtimal. Ek olarak SSH'ın herkese açık kısa dönem rezervasyon
  portalı (booking.sshxl.nl) da izleniyor. Uzun dönem için SSH hesabınızla giriş yapıp
  kendi arama bildirimlerini kurmanız önerilir.
- **seen.json commit'leri:** Bot her yeni ilanda repoya küçük bir commit atar; commit
  geçmişinin kalabalıklaşması normaldir. Bu commit'ler repo "aktivitesi" sayıldığı için
  GitHub'ın 60 gün hareketsizlikte zamanlanmış workflow'ları durdurma kuralına da takılmazsınız.
- **Huurportaal** için verilen link tek bir ilan sayfasıydı; onun yerine sitenin Tilburg
  listesi (`/en/for-rent/tilburg`) izleniyor.
- **Plaza** tüm şehirleri listeler (Delft, Bochum dahil). Sadece belirli şehirler için
  [watcher.py](watcher.py) içindeki `SITES` bölümünde ilgili siteye örn.
  `"cities": ["Tilburg"]` yazın. Site eklemek/çıkarmak da aynı listeden yapılır.
- Bir site tasarımını değiştirirse ilgili `link_pattern` güncellenmelidir
  (yerelde `--dry-run --dump` çalıştırıp `logs/dump/` altındaki HTML'e bakarak).

## Yerelde test (isteğe bağlı)

```bash
cd "/Users/yilmazsahin/Desktop/My Zone/rental-watcher" && .venv/bin/python watcher.py --dry-run
```

Telegram bağlantısını denemek için `config.example.json` → `config.json` kopyalayıp
doldurun ve:

```bash
cd "/Users/yilmazsahin/Desktop/My Zone/rental-watcher" && .venv/bin/python watcher.py --test
```

Mac'te launchd ile yerel çalıştırma da mümkündür (GitHub yerine):
`com.rental-watcher.plist` dosyasını `~/Library/LaunchAgents/` içine kopyalayıp
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rental-watcher.plist`
komutunu çalıştırın — ancak Mac uykudayken kontrol yapılmaz.
