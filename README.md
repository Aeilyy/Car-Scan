# CarScan

Aplicație Android + server pe laptop. Fotografiezi talonul, numele proprietarului, VIN-ul și
numărul de înmatriculare sunt citite **local pe laptop** (fără internet, fără API plătit),
adaugi descrierea, apeși Send, iar totul ajunge în fișierul Excel descărcat cu al doilea buton.

```
telefon: aplicația CarScan ──NetBird──▶ laptop: server/app.py (port 8000)
                                          ├─ RapidOCR (model local, ~14 MB)
                                          ├─ data/entries.db  (toate intrările)
                                          └─ data/photos/     (pozele la rezoluție mare)
```

```
carscan/
├─ server/        serverul Python (rulează pe laptop)
├─ mobile/        aplicația de telefon (Capacitor)
│  ├─ src/        interfața (HTML/CSS/JS)
│  └─ android/    proiectul Android (deschis în Android Studio sau construit de GitHub)
├─ deploy/        pornire automată la boot (Linux / macOS)
└─ .github/       construiește APK-ul automat pe GitHub
```

---

## 1. Laptopul (serverul)

**Să nu intre în sleep** (și să meargă cu capacul închis):

- Linux
  ```bash
  sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/; s/^#\?HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf
  sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
  sudo systemctl restart systemd-logind
  ```
- macOS
  ```bash
  sudo pmset -a sleep 0 disksleep 0 autorestart 1
  sudo pmset -a disablesleep 1
  ```

**Instalare:**
```bash
cd ~/carscan/server
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./run.sh
```
Nu e nevoie de `.env`; setările implicite merg. Modelul OCR vine în pachetul pip, nu se
descarcă nimic separat.

**Pornire automată la boot:** Linux: editează `deploy/carscan.service` (user + căi), apoi
```bash
sudo cp deploy/carscan.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now carscan
```
macOS: editează calea din `deploy/com.carscan.server.plist`, apoi
```bash
cp deploy/com.carscan.server.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.carscan.server.plist
```

## 2. Acces de oriunde: NetBird

Nu mai există parolă, așa că serverul **nu trebuie** expus pe internet. Doar dispozitivele
din rețeaua ta NetBird ajung la el.

1. Laptop: `curl -fsSL https://pkgs.netbird.io/install.sh | sh` apoi `sudo netbird up`
2. Telefon: aplicația **NetBird** din Play Store, același cont.
3. Pe laptop: `netbird status` → notează **IP-ul NetBird** (ex. `100.92.14.7`).
4. Firewall (Linux), ca portul 8000 să fie accesibil doar prin NetBird:
   ```bash
   sudo ufw allow in on wt0 to any port 8000 proto tcp
   sudo ufw allow ssh
   sudo ufw enable
   ```
   Fără pasul ăsta, oricine din rețeaua locală a laptopului poate folosi aplicația.

## 3. Aplicația Android (APK)

Varianta cea mai simplă, fără să instalezi nimic:

1. Fă un repo pe GitHub (poate fi privat) și urcă tot folderul `carscan`.
2. Tab-ul **Actions** → rularea „Build Android app” pornește singură (~5 min).
3. Când e verde, jos la **Artifacts** descarci `CarScan-apk` → în zip e `app-debug.apk`.
4. Trimite APK-ul pe telefon, deschide-l, permite „Instalare din surse necunoscute”.

Alternativ, cu Android Studio: `cd mobile && npm install && npm run android`, apoi deschizi
folderul `mobile/android` în Android Studio → Build → Build APK(s).

**La prima pornire** aplicația cere adresa serverului: scrii IP-ul NetBird, ex.
`100.92.14.7:8000`, și apeși Connect. Se ține minte; o schimbi apăsând adresa de jos,
de pe ecranul principal.

Butonul de Excel descarcă fișierul și deschide meniul de share: alegi Excel, Drive,
WhatsApp, Files etc.

### Schimbări în aplicație
Editezi `mobile/src/`, faci push, GitHub construiește un APK nou; îl instalezi peste cel vechi.
Interfața e servită și de laptop la `http://<ip>:8000`, util pentru test din browser.

### iPhone
Proiectul e Capacitor, deci merge și pe iOS (`npx cap add ios`), dar ai nevoie de un Mac cu
Xcode, iar fără cont Apple Developer (99 $/an) aplicația instalată expiră după 7 zile.

## 4. Cum citește talonul

**Implicit: RapidOCR** (modelele PaddleOCR PP-OCRv3 în format ONNX, ~14 MB, câteva milioane
de parametri, sub 1 secundă pe poză pe un CPU vechi). Textul citit e potrivit pe câmpurile
certificatului de înmatriculare după etichete și poziție:

- **A** → număr de înmatriculare (verificat pe formatele românești, corectează O/0, I/1)
- **E** → VIN (17 caractere, fără I/O/Q)
- **C.2.1 + C.2.2** dacă proprietarul e altul decât deținătorul, altfel **C.1.1 + C.1.2**

Limitări: OCR-ul pierde diacriticele (ș → s) și uneori spațiile din nume
(„SCAUTOSERVICESRL”). Câmpurile suspecte apar galbene în aplicație; toate se pot edita.

**Opțional: model AI vizual local (Ollama)**, dacă OCR-ul nu-ți ajunge (talon fotografiat
strâmb, alte documente). Mai „deștept”, dar mult mai lent pe un laptop vechi
(zeci de secunde pe poză) și cere ~4 GB RAM liber.
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3-vl:2b
```
Apoi în `server/.env`: `OCR_ENGINE=ollama` și repornește serverul.

## 5. Excel

| Date & time | Owner | VIN (serial number) | Licence plate | Description | Photo |
|---|---|---|---|---|---|
| 17.10.2026 19:21 | POPESCU ION | WVWZZZAUZJW123456 | CS 07 XYZ | broken transmission | *(poza)* |
| | | | | | |
| 17.10.2026 19:25 | … | … | … | … | *(poza)* |

Un rând gol între intrări, poza talonului în fiecare rând. Fișierul se regenerează din baza
de date la fiecare descărcare, deci conține mereu tot.

## 6. Backup

Tot ce contează e în `server/data/`. Copiază folderul periodic pe un stick sau în cloud.

## Probleme

| Problemă | Rezolvare |
|---|---|
| „Can't reach the server” | NetBird conectat pe telefon? `netbird status` și `curl localhost:8000/api/ping` pe laptop. |
| Câmpuri goale după scanare | Poza: talonul plat, lumină bună, fără reflexii, să umple cadrul. |
| Nu se instalează APK-ul | Permite instalarea din surse necunoscute pentru aplicația cu care îl deschizi. |
