import React, { useEffect, useRef, useState } from 'react';
import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';

export default function RealWorldMap({ location, onClose }) {
  const cesiumContainer = useRef(null);
  const viewerRef = useRef(null);
  const [error, setError] = useState(null);

  const pinColorForScore = (score) => {
    const numeric = Number(score);
    if (!Number.isFinite(numeric)) return Cesium.Color.fromCssColorString('#00f3ff');
    if (numeric >= 85) return Cesium.Color.LIME;
    if (numeric >= 70) return Cesium.Color.YELLOW;
    if (numeric >= 50) return Cesium.Color.ORANGE;
    return Cesium.Color.RED;
  };

  useEffect(() => {
    if (!cesiumContainer.current) return;
    let isDisposed = false;

    // Set Ion token for Cesium assets (optional, but helps with base terrain)
    // The Google Photorealistic 3D Tiles API key is separate.
    Cesium.Ion.defaultAccessToken = import.meta.env.VITE_CESIUM_ION_TOKEN || ''; 

    try {
      // Initialize the viewer
      const viewer = new Cesium.Viewer(cesiumContainer.current, {
        animation: false,
        baseLayerPicker: false,
        fullscreenButton: false,
        geocoder: false,
        homeButton: false,
        infoBox: false,
        sceneModePicker: false,
        selectionIndicator: false,
        timeline: false,
        navigationHelpButton: false,
        navigationInstructionsInitiallyVisible: false,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
        globe: true,
      });
      viewerRef.current = viewer;

      // Hide the logo
      viewer.cesiumWidget.creditContainer.style.display = "none";

      const loadGoogleTiles = async () => {
        const mapsKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY || '';
        if (!mapsKey) {
          setError('Google 3D tiles key is missing. Showing base globe fallback.');
          return;
        }
        try {
          const tileset = await Cesium.createGooglePhotorealistic3DTileset({
            key: mapsKey,
          });
          if (isDisposed || !viewerRef.current || viewerRef.current.isDestroyed()) return;
          viewer.scene.primitives.add(tileset);
        } catch (e) {
          console.error('Failed to load Google Photorealistic 3D Tiles', e);
          if (isDisposed || !viewerRef.current || viewerRef.current.isDestroyed()) return;
          setError('Could not load Photorealistic 3D tiles (check Google 3D Tiles API + key restrictions). Displaying base globe fallback.');
        }
      };

      loadGoogleTiles();

      // Fly to the location
      if (location && location.latitude && location.longitude) {
        const lat = Number(location.latitude);
        const lon = Number(location.longitude);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

        const destination = Cesium.Cartesian3.fromDegrees(
          lon,
          lat,
          Number(location.elevation) || 1500 // defaults to 1.5km height
        );
        
        viewer.camera.flyTo({
          destination: destination,
          orientation: {
            heading: Cesium.Math.toRadians(0.0),
            pitch: Cesium.Math.toRadians(-45.0),
            roll: 0.0
          },
          duration: 3.0
        });

        // Add a pin
        viewer.entities.add({
          position: destination,
          point: {
            pixelSize: 20,
            color: pinColorForScore(location.score),
            outlineColor: Cesium.Color.WHITE,
            outlineWidth: 3
          },
          label: {
            text: location.name || 'Best Sky Spot',
            font: '14pt monospace',
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            outlineWidth: 2,
            verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            pixelOffset: new Cesium.Cartesian2(0, -30)
          }
        });
      }

      return () => {
        isDisposed = true;
        if (viewerRef.current) {
          viewerRef.current.destroy();
          viewerRef.current = null;
        }
      };
    } catch (e) {
      console.error(e);
      setError('An error occurred initializing the 3D map viewer.');
    }
  }, [location]);

  return (
    <div className="absolute inset-0 z-40 bg-[#000]">
      <div className="absolute top-24 left-6 z-50">
        <button 
          onClick={onClose}
          className="bg-black/60 backdrop-blur-md border border-cyan-400/30 text-cyan-400 hover:bg-cyan-900/30 font-mono text-sm uppercase px-6 py-3 rounded tracking-widest transition-all shadow-[0_0_20px_rgba(0,243,255,0.15)]"
        >
          ← Return to Space View
        </button>
      </div>

      {error && (
        <div className="absolute top-24 left-1/2 -translate-x-1/2 z-50 bg-red-900/80 text-white px-6 py-3 rounded border border-red-500/50 backdrop-blur-sm text-sm font-mono text-center">
          {error}
        </div>
      )}

      {/* Render right-side overlay panel if location data is provided */}
      {location && (
        <div className="absolute top-24 right-6 bottom-12 w-80 bg-slate-950/80 backdrop-blur-3xl border border-white/10 rounded-2xl p-6 shadow-2xl z-50 flex flex-col gap-6 overflow-y-auto text-white">
          <div>
            <h2 className="text-2xl font-mono font-bold uppercase text-white mb-1 leading-tight">{location.name || 'Optimal Spot'}</h2>
            <div className="text-xs text-gray-400 uppercase tracking-widest mb-4">
              {location.distance_km ? `${location.distance_km.toFixed(1)} km away` : 'Selected Location'}
            </div>
            
            <div className="flex items-end justify-between border-b border-white/10 pb-4">
              <span className="text-sm font-mono text-gray-400 uppercase">Visibility</span>
              <span className="text-3xl font-bold text-cyan-400">{location.score || '—'}</span>
            </div>
          </div>
          
          <div className="space-y-4 text-sm font-mono">
            <div className="flex justify-between items-center bg-white/5 p-3 rounded">
              <span className="text-gray-400">COORDS</span>
              <span className="text-white text-right">
                {location.latitude?.toFixed(4)}<br/>{location.longitude?.toFixed(4)}
              </span>
            </div>

            <div className="flex justify-between items-center bg-white/5 p-3 rounded">
              <span className="text-gray-400">BORTLE</span>
              <span className="text-white font-bold">{location.bortle_class || '—'}</span>
            </div>

            <div className="flex justify-between items-center bg-white/5 p-3 rounded">
              <span className="text-gray-400">OPENNESS</span>
              <span className="text-white uppercase">{location.openness || '—'}</span>
            </div>
            
            <div className="flex justify-between items-center bg-white/5 p-3 rounded">
              <span className="text-gray-400">ELEVATION</span>
              <span className="text-white">{location.elevation || '—'}</span>
            </div>

            {(location.best_spot_name || location.distance_from_optimal_km != null || location.best_spot_distance_km != null) && (
              <div className="flex justify-between items-center bg-cyan-500/10 border border-cyan-400/30 p-3 rounded">
                <span className="text-cyan-200">BEST SPOT</span>
                <span className="text-right text-cyan-100">
                  {location.best_spot_name || location.name || '—'}
                  <br />
                  <span className="text-xs text-cyan-200/80">
                    {location.best_spot_distance_km != null
                      ? `${location.best_spot_distance_km} km from optimal`
                      : location.distance_from_optimal_km != null
                        ? `${location.distance_from_optimal_km} km from optimal`
                        : `Score ${location.best_spot_score ?? location.score ?? '—'}`}
                  </span>
                </span>
              </div>
            )}
          </div>

          <div className="mt-auto pt-4 flex flex-col gap-3">
             <a 
                href={`https://www.google.com/maps/search/?api=1&query=${location.latitude},${location.longitude}`} 
                target="_blank" 
                rel="noreferrer"
                className="block w-full text-center py-3 bg-white/10 hover:bg-white/20 rounded font-mono text-sm uppercase tracking-widest transition-all"
             >
               Open In Google Maps
             </a>
          </div>
        </div>
      )}

      {/* The container for the Cesium map */}
      <div 
        ref={cesiumContainer} 
        style={{ width: '100%', height: '100%' }}
        className="[&_.cesium-widget-credits]:hidden"
      />
    </div>
  );
}
