import React, { useEffect, useRef, useState } from 'react';
import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';

export default function RealWorldMap({ location, onClose }) {
  const cesiumContainer = useRef(null);
  const viewerRef = useRef(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!cesiumContainer.current) return;
    
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
        globe: false, // We hide the default globe since we use Google 3D Tiles
      });
      viewerRef.current = viewer;

      // Hide the logo
      viewer.cesiumWidget.creditContainer.style.display = "none";

      const loadGoogleTiles = async () => {
        try {
          const tileset = await Cesium.createGooglePhotorealistic3DTileset({
            key: import.meta.env.VITE_GOOGLE_MAPS_API_KEY || '',
          });
          viewer.scene.primitives.add(tileset);
        } catch (e) {
          console.error('Failed to load Google Photorealistic 3D Tiles', e);
          // Fallback to a basic globe if key is missing/invalid
          viewer.scene.globe = new Cesium.Globe(Cesium.Ellipsoid.WGS84);
          viewer.scene.globe.show = true;
          setError('Could not load Photorealistic 3D tiles. Displaying base globe as fallback.');
        }
      };

      loadGoogleTiles();

      // Fly to the location
      if (location && location.latitude && location.longitude) {
        const destination = Cesium.Cartesian3.fromDegrees(
          location.longitude,
          location.latitude,
          location.elevation || 1500 // defaults to 1.5km height
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
            color: Cesium.Color.fromCssColorString('#00f3ff'),
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
