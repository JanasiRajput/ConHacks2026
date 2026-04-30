import React, { useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { ArrowLeft, ExternalLink, MapPin, Navigation } from 'lucide-react';

/**
 * LocationDetail
 *
 * Drop-in replacement for the old Cesium globe. Renders a smooth
 * Leaflet map (loaded from CDN — no npm install needed) with an
 * animated pin and a compact info card.
 *
 * Props:
 *   location - { name, latitude, longitude, score?, ... } (required)
 *   onClose  - close handler
 */

const PIN_STYLES = `
  .nightowl-pin-wrap {
    position: relative;
    width: 44px;
    height: 44px;
    pointer-events: none;
  }
  .nightowl-pin-wrap .nightowl-pulse {
    position: absolute;
    inset: 0;
    border-radius: 9999px;
    background: rgba(34, 211, 238, 0.32);
    box-shadow: 0 0 22px rgba(34, 211, 238, 0.55);
    animation: nightowl-pulse 1.8s ease-out infinite;
  }
  .nightowl-pin-wrap .nightowl-pulse.delay {
    animation-delay: 0.9s;
  }
  .nightowl-pin-wrap .nightowl-dot {
    position: absolute;
    left: 50%;
    top: 50%;
    width: 14px;
    height: 14px;
    margin-left: -7px;
    margin-top: -7px;
    border-radius: 9999px;
    background: linear-gradient(135deg, #67e8f9, #22d3ee);
    border: 2px solid #ffffff;
    box-shadow: 0 0 10px rgba(34, 211, 238, 0.95), 0 4px 10px rgba(0, 0, 0, 0.4);
  }
  @keyframes nightowl-pulse {
    0%   { transform: scale(0.6); opacity: 0.85; }
    80%  { transform: scale(2.2); opacity: 0; }
    100% { transform: scale(2.2); opacity: 0; }
  }

  /* Make Leaflet panes blend with the dark UI without overriding tiles. */
  .leaflet-container {
    background: #03030b;
    font-family: inherit;
  }
  .leaflet-control-attribution {
    background: rgba(3, 3, 11, 0.7) !important;
    color: rgba(226, 232, 240, 0.7) !important;
    font-size: 10px !important;
  }
  .leaflet-control-attribution a {
    color: rgba(103, 232, 249, 0.85) !important;
  }
  .leaflet-bar a {
    background: rgba(3, 3, 11, 0.85) !important;
    color: #67e8f9 !important;
    border-color: rgba(103, 232, 249, 0.35) !important;
  }
  .leaflet-bar a:hover {
    background: rgba(8, 17, 31, 0.95) !important;
  }
`;

function ensureStyleInjected() {
  if (typeof document === 'undefined') return;
  if (document.getElementById('nightowl-map-styles')) return;
  const style = document.createElement('style');
  style.id = 'nightowl-map-styles';
  style.textContent = PIN_STYLES;
  document.head.appendChild(style);
}

function waitForLeaflet(timeoutMs = 7000) {
  return new Promise((resolve, reject) => {
    if (typeof window === 'undefined') {
      reject(new Error('No window'));
      return;
    }
    if (window.L) {
      resolve(window.L);
      return;
    }
    const start = Date.now();
    const poll = () => {
      if (window.L) {
        resolve(window.L);
        return;
      }
      if (Date.now() - start > timeoutMs) {
        reject(new Error('Leaflet failed to load from CDN'));
        return;
      }
      setTimeout(poll, 80);
    };
    poll();
  });
}

function scoreColor(score) {
  const n = Number(score);
  if (!Number.isFinite(n)) return 'text-cyan-300';
  if (n >= 85) return 'text-lime-300';
  if (n >= 70) return 'text-yellow-300';
  if (n >= 50) return 'text-orange-300';
  return 'text-red-300';
}

export default function LocationDetail({ location, onClose }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [error, setError] = useState(null);

  const lat = Number(location?.latitude);
  const lon = Number(location?.longitude);
  const valid = Number.isFinite(lat) && Number.isFinite(lon);

  const score = useMemo(() => {
    const n = Number(location?.score);
    return Number.isFinite(n) ? n : null;
  }, [location]);

  useEffect(() => {
    ensureStyleInjected();
  }, []);

  useEffect(() => {
    if (!valid || !containerRef.current) return;

    let cancelled = false;
    let map;
    let resizeObserver;

    waitForLeaflet()
      .then((L) => {
        if (cancelled || !containerRef.current) return;

        map = L.map(containerRef.current, {
          center: [lat, lon],
          zoom: 12,
          zoomControl: true,
          attributionControl: true,
          preferCanvas: true,
          fadeAnimation: true,
          zoomAnimation: true,
        });
        mapRef.current = map;

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
          maxZoom: 19,
          crossOrigin: true,
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);

        const pinIcon = L.divIcon({
          className: '',
          html:
            '<div class="nightowl-pin-wrap">' +
            '<div class="nightowl-pulse"></div>' +
            '<div class="nightowl-pulse delay"></div>' +
            '<div class="nightowl-dot"></div>' +
            '</div>',
          iconSize: [44, 44],
          iconAnchor: [22, 22],
        });

        L.marker([lat, lon], { icon: pinIcon, keyboard: false }).addTo(map);

        // Slight smooth fly-in feel.
        map.flyTo([lat, lon], 13, { duration: 0.7 });

        // Leaflet needs a size recalc once the modal finishes its open
        // transition, otherwise tiles render gray.
        setTimeout(() => map && map.invalidateSize(), 350);

        if (typeof ResizeObserver !== 'undefined' && containerRef.current) {
          resizeObserver = new ResizeObserver(() => {
            if (mapRef.current) mapRef.current.invalidateSize();
          });
          resizeObserver.observe(containerRef.current);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message || 'Map could not load');
      });

    return () => {
      cancelled = true;
      if (resizeObserver) resizeObserver.disconnect();
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, [lat, lon, valid]);

  return (
    <div className="absolute inset-0 z-40 bg-[#03030b]">
      {/* Map fills the screen */}
      <div ref={containerRef} className="absolute inset-0 z-0" />

      {/* Top-left close button */}
      <div className="absolute top-5 left-5 z-30">
        <button
          type="button"
          onClick={onClose}
          className="flex items-center gap-2 bg-black/70 backdrop-blur-md border border-cyan-400/30 text-cyan-200 hover:bg-cyan-900/40 font-mono text-xs uppercase px-4 py-2.5 rounded-lg tracking-widest transition-all shadow-[0_8px_24px_rgba(0,0,0,0.4)]"
        >
          <ArrowLeft className="w-4 h-4" />
          Return
        </button>
      </div>

      {/* Top-right title chip */}
      <div className="absolute top-5 right-5 z-30 bg-black/70 backdrop-blur-md border border-cyan-400/25 rounded-lg px-4 py-2 max-w-[60vw] shadow-[0_8px_24px_rgba(0,0,0,0.4)]">
        <div className="flex items-center gap-2">
          <MapPin className="w-3.5 h-3.5 text-cyan-300" />
          <span className="text-[10px] uppercase tracking-[0.3em] text-cyan-300/70 font-mono">
            Site
          </span>
        </div>
        <div className="text-sm font-bold text-white truncate">
          {location?.name || 'Selected Location'}
        </div>
      </div>

      {/* Bottom-left info card */}
      {valid && (
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.1 }}
          className="absolute bottom-6 left-5 z-30 w-[320px] max-w-[90vw] bg-slate-950/85 backdrop-blur-xl border border-white/10 rounded-2xl p-5 shadow-[0_24px_60px_rgba(0,0,0,0.5)] text-white"
        >
          <div className="flex items-start justify-between gap-3 mb-4">
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-[0.3em] text-cyan-300/80 font-mono">
                Visibility
              </div>
              <div className={`text-4xl font-bold leading-none mt-1 ${scoreColor(score)}`}>
                {score ?? '—'}
                {score != null && (
                  <span className="text-base text-slate-400 align-top ml-1">/100</span>
                )}
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] uppercase tracking-[0.25em] text-slate-400 font-mono">
                Distance
              </div>
              <div className="text-base font-bold text-white mt-1">
                {location?.distance_km != null
                  ? `${Number(location.distance_km).toFixed(1)} km`
                  : '—'}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs font-mono mb-4">
            <Stat label="Lat" value={lat.toFixed(4)} />
            <Stat label="Lon" value={lon.toFixed(4)} />
            <Stat label="Bortle" value={location?.bortle_class ?? '—'} />
            <Stat label="Elev" value={location?.elevation ?? '—'} />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <a
              href={`https://www.google.com/maps/search/?api=1&query=${lat},${lon}`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-center gap-1.5 py-2 bg-white/8 hover:bg-white/15 rounded-lg font-mono text-[10px] uppercase tracking-widest transition-all"
            >
              <ExternalLink className="w-3 h-3" />
              Google
            </a>
            <a
              href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=13/${lat}/${lon}`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center justify-center gap-1.5 py-2 bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-400/30 text-cyan-100 rounded-lg font-mono text-[10px] uppercase tracking-widest transition-all"
            >
              <Navigation className="w-3 h-3" />
              OSM
            </a>
          </div>
        </motion.div>
      )}

      {!valid && (
        <div className="absolute inset-0 z-20 flex items-center justify-center text-slate-300 font-mono text-sm">
          No coordinates available for this location.
        </div>
      )}

      {error && (
        <div className="absolute top-24 left-1/2 -translate-x-1/2 z-30 bg-red-900/80 text-white px-5 py-2.5 rounded-lg border border-red-500/50 backdrop-blur-sm text-xs font-mono shadow-lg">
          {error}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="bg-white/5 border border-white/5 rounded-lg p-2.5 flex items-center justify-between">
      <span className="text-slate-400 text-[9px] tracking-widest uppercase">{label}</span>
      <span className="text-white font-bold">{value ?? '—'}</span>
    </div>
  );
}
