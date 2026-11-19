# Calcolatore Profili

Web app (Flask + SQLite) per il calcolo di schede, tappi e taglio aste di ottone da 3000 mm.

## Installazione (amd64 o arm64)

Compose (su OMV: incollare nel plugin Compose):

    services:
      calcolatore:
        image: ghcr.io/ciux23/calcolatore-profili:latest
        container_name: calcolatore-profili
        ports:
          - "8082:5000"
        volumes:
          - ./data:/data
        restart: unless-stopped

Aprire http://IP:8082

Il database resta in ./data/profili.db (mai nel repo). Per migrarlo su un'altra macchina, copiare quel file.

## Aggiornamenti

Le modifiche si fanno direttamente su GitHub (editor web). Ogni commit su main genera una build GitHub Actions multi-arch (amd64 + arm64) che pubblica latest. Con Watchtower il container si aggiorna da solo.

## Versioni

Release con tag v1.0.0 genera immagini taggate 1.0.0, 1.0, 1. Ogni build ha anche un tag sha-xxxxxxx per rollback.
