import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Wind, BarChart2, MapPin, Calendar, Clock, Search, ChevronRight, ChevronLeft, Loader2, Route, Telescope, Camera } from 'lucide-react';
import { getPlan, getEvents, getNearby, getAutocomplete, geocode, getFuture, getAstronomy } from '../services/api';
import LocationDetail from './LocationDetail';

const fadeInUp = {
  initial: { opacity: 0, y: 30 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-100px" },
  transition: { duration: 0.6, ease: "easeOut" }
};

/* ---- helpers ---- */
function nowISO() {
  const d = new Date();
  return {
    date: d.toISOString().slice(0, 10),
    time: d.toTimeString().slice(0, 5),
  };
}

export default function Overlay() {
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [activeCard, setActiveCard] = useState(0);
  const [error, setError] = useState(null);

  /* --- Map Mode --- */
  const [mapMode, setMapMode] = useState('space'); // 'space' | 'real_world'
  const [selectedMapLocation, setSelectedMapLocation] = useState(null);

  /* --- Autocomplete via Backend Proxy --- */
  const [predictions, setPredictions] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const inputRef = useRef(null);

  // Cancellation + debounce for autocomplete. Without these, every
  // keystroke fires a Nominatim call -> hits their 1 req/sec rate
  // limit -> the dropdown silently never updates.
  const abortControllerRef = useRef(null);
  const debounceTimerRef = useRef(null);

  const runAutocomplete = useCallback(async (input) => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const results = await getAutocomplete(input, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (results && results.length > 0) {
        setPredictions(results);
        setShowDropdown(true);
      } else {
        setPredictions([]);
        setShowDropdown(false);
      }
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      console.error('Autocomplete error:', err);
      setPredictions([]);
    }
  }, []);

  const handleInputChange = (e) => {
    const value = e.target.value;
    setSearchQuery(value);

    if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    if (!value.trim()) {
      if (abortControllerRef.current) abortControllerRef.current.abort();
      setPredictions([]);
      setShowDropdown(false);
      return;
    }
    debounceTimerRef.current = setTimeout(() => runAutocomplete(value), 320);
  };

  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      if (abortControllerRef.current) abortControllerRef.current.abort();
    };
  }, []);

  const selectPrediction = (prediction) => {
    setSearchQuery(prediction.description);
    setPredictions([]);
    setShowDropdown(false);
    // Auto-trigger search
    geocodeAndSearch(prediction.description);
  };

  /* --- API state --- */
  const [planData, setPlanData] = useState(null);
  const [eventsData, setEventsData] = useState(null);
  const [nearbyData, setNearbyData] = useState(null);
  const [futureData, setFutureData] = useState(null);
  const [astronomyData, setAstronomyData] = useState(null);
  const [locationLabel, setLocationLabel] = useState('');
  const [activeCoords, setActiveCoords] = useState(null);
  const [customDate, setCustomDate] = useState(nowISO().date);
  const [customTime, setCustomTime] = useState(nowISO().time);
  const [planningCustom, setPlanningCustom] = useState(false);

  /* --- geocode + search --- */
  const geocodeAndSearch = useCallback(async (query) => {
    setIsSearching(true);
    setError(null);
    setShowDropdown(false);

    try {
      let latitude, longitude, name;
      const resolved = await geocode(query);
      latitude = resolved.latitude;
      longitude = resolved.longitude;
      name = resolved.name;

      setLocationLabel(name);
      setActiveCoords({ latitude, longitude });
      const { date, time } = nowISO();

      const [plan, events, nearby, future, astronomy] = await Promise.all([
        getPlan({ latitude, longitude, locationName: name, date, time }).catch(() => null),
        getEvents({ latitude, longitude, date, time }).catch(() => null),
        getNearby({ latitude, longitude, locationName: name }).catch(() => null),
        getFuture({ latitude, longitude, locationName: name, days: 7 }).catch(() => null),
        getAstronomy({ latitude, longitude, date, time }).catch(() => null),
      ]);

      setPlanData(plan);
      setEventsData(events);
      setNearbyData(nearby);
      setFutureData(future);
      setAstronomyData(astronomy);
    } catch (err) {
      console.error(err);
      setError(err.message || 'Something went wrong — is the backend running?');
    } finally {
      setIsSearching(false);
    }
  }, []);

  const handleSearch = (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    geocodeAndSearch(searchQuery);
  };

  /* --- refs for floating physics & card carousel --- */
  const containerRef = useRef(null);
  const cardContainerRef = useRef(null);
  const imageRefs = useRef([]);

  const handleNavClick = (index) => {
    setActiveCard(index);
    if (containerRef.current) {
      containerRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  };

  const floatingItemsRef = useRef([
    { src: '/elementsinspace/1.png', x: 50, y: 150, vx: 0.4, vy: 0.3, w: 220, h: 220 },
    { src: '/elementsinspace/2.png', x: 100, y: 600, vx: -0.3, vy: 0.4, w: 220, h: 220 },
    { src: '/elementsinspace/4.png', x: 900, y: 200, vx: 0.4, vy: -0.3, w: 220, h: 220 },
    { src: '/elementsinspace/5.png', x: 800, y: 650, vx: -0.4, vy: -0.4, w: 220, h: 220 },
  ]);

  /* --- card data: LIVE values from API or fallback to mock --- */
  const conditionsItems = planData
    ? [
        { label: 'Air Quality (AQI)', value: String(planData.air_quality?.aqi ?? '—'), desc: planData.air_quality?.category ?? '' },
        { label: 'Light Pollution Index', value: `Class ${planData.light_pollution?.bortle_class ?? '—'}`, desc: 'Bortle Scale' },
        { label: 'Cloud Forecast', value: `${planData.weather?.cloud_cover ?? '—'}%`, desc: (planData.weather?.cloud_cover ?? 100) <= 25 ? 'Clear' : 'Cloudy' },
        { label: 'Moon Illumination', value: `${planData.astronomy?.moon_illumination ?? '—'}%`, desc: planData.astronomy?.moon_phase ?? '' },
        { label: 'Atmospheric Clarity', value: `${Math.max(0, 100 - (planData.weather?.humidity ?? 0))}%`, desc: (planData.weather?.humidity ?? 0) < 50 ? 'Excellent' : 'Moderate' },
      ]
    : [
        { label: 'Air Quality (AQI)', value: '—', desc: 'Search to load' },
        { label: 'Light Pollution Index', value: '—', desc: '' },
        { label: 'Cloud Forecast', value: '—', desc: '' },
        { label: 'Moon Illumination', value: '—', desc: '' },
        { label: 'Atmospheric Clarity', value: '—', desc: '' },
      ];

  const visScore = planData?.visibility_score ?? '—';
  const visItems = planData
    ? [
        { label: 'Stellar Visibility Probability', value: planData.breakdown?.astronomy ?? 0 },
        { label: 'Milky Way Visibility', value: planData.astronomy?.milky_way_visible ? 85 : 15 },
        { label: 'Aurora Probability', value: planData.aurora?.visibility_probability ?? 0 },
      ]
    : [
        { label: 'Stellar Visibility Probability', value: 0 },
        { label: 'Milky Way Visibility', value: 0 },
        { label: 'Aurora Probability', value: 0 },
      ];

  const optimalLocation = nearbyData?.optimal_coordinates || null;
  const bestSpotNearOptimal = nearbyData?.best_spot || null;

  const locationsList = nearbyData?.best_locations?.slice(0, 3) ?? [
    { name: 'Search a location to load', score: '—', distance: '' },
  ];

  const eventsList = eventsData
    ? [
        { event: 'Aurora Chance', value: eventsData.aurora?.aurora_chance ?? '—' },
        ...(eventsData.sky_events?.active_meteor_shower
          ? [{ event: eventsData.sky_events.active_meteor_shower.name, value: 'Active Now' }]
          : []),
        ...(eventsData.sky_events?.visible_planets?.slice(0, 2).map(p => ({
          event: `${p.name} Visible`, value: `Alt ${p.altitude?.toFixed(1) ?? '—'}°`
        })) ?? []),
      ]
    : [
        { event: 'Northern Lights Probability', value: '—' },
        { event: 'Perseid Meteor Shower', value: '—' },
      ];

  const forecastItems = planData
    ? [
        { label: 'Best Observation Window', value: planData.best_window },
        ...(planData.ai_summary ? [{ label: 'AI Recommendation', value: planData.ai_summary }] : []),
        { label: 'Sky Quality', value: `${planData.sky_quality} — ${planData.recommendation}` },
      ]
    : [
        { label: 'Best Time Tonight', value: '—' },
        { label: 'Best Night This Week', value: '—' },
        { label: 'Recommended Window', value: '—' },
      ];

  const futureItems = futureData?.results?.slice(0, 5) ?? [];
  const astronomyPlanets = astronomyData?.astronomy?.planets ?? [];
  const astronomyStars = astronomyData?.astronomy?.stars ?? [];
  const astronomyConsts = astronomyData?.astronomy?.constellations ?? [];

  const directionItems = [
    {
      label: 'Aurora',
      altitude: planData?.astronomy?.aurora_altitude ?? null,
      azimuth: planData?.astronomy?.aurora_azimuth ?? null,
      source: 'Live /plan',
    },
    {
      label: 'Milky Way Core',
      altitude: planData?.astronomy?.milky_way_core_altitude ?? null,
      azimuth: planData?.astronomy?.milky_way_core_azimuth ?? null,
      source: 'Live /plan',
    },
    {
      label: 'Moon',
      altitude: planData?.astronomy?.moon_altitude ?? null,
      azimuth: planData?.astronomy?.moon_azimuth ?? null,
      source: 'Live /plan',
    },
    {
      label: 'Sun',
      altitude: planData?.astronomy?.sun_altitude ?? null,
      azimuth: planData?.astronomy?.sun_azimuth ?? null,
      source: 'Live /plan',
    },
  ].filter((item) => item.altitude != null && item.azimuth != null);

  const cameraCards = planData?.camera_settings
    ? Object.entries(planData.camera_settings)
        .filter(([, val]) => val !== null && val !== undefined && `${val}`.trim() !== '')
        .map(([key, val]) => ({
          key: key.replace(/_/g, ' ').toUpperCase(),
          value: String(val),
        }))
    : [];

  const runCustomPlan = async () => {
    if (!activeCoords?.latitude || !activeCoords?.longitude || !customDate || !customTime) return;
    setPlanningCustom(true);
    try {
      const plan = await getPlan({
        latitude: activeCoords.latitude,
        longitude: activeCoords.longitude,
        locationName: locationLabel || null,
        date: customDate,
        time: customTime,
      });
      setPlanData(plan);
      const astronomy = await getAstronomy({
        latitude: activeCoords.latitude,
        longitude: activeCoords.longitude,
        date: customDate,
        time: customTime,
      }).catch(() => null);
      setAstronomyData(astronomy);
    } catch (err) {
      setError(err.message || 'Failed to run plan for selected date/time');
    } finally {
      setPlanningCustom(false);
    }
  };

  const cards = [
    {
      id: 'conditions',
      title: '01 // Conditions Module',
      icon: Wind,
      color: 'from-cyan-500/20 to-blue-500/10',
      accent: 'text-cyan-400',
      content: (
        <div className="divide-y divide-white/5 font-card text-lg">
          {conditionsItems.map((item, index) => (
            <div key={index} className="py-4 px-4 mb-3 flex items-center justify-between rounded-[1.5rem] bg-white/5 border border-white/10 shadow-[0_14px_32px_rgba(0,100,180,0.06)] backdrop-blur-md">
              <span className="text-gray-300 uppercase text-sm">{item.label}</span>
              <div className="text-right">
                <span className="text-white font-bold text-xl">{item.value}</span>
                <span className="block text-sm text-gray-400 uppercase tracking-wider">{item.desc}</span>
              </div>
            </div>
          ))}
        </div>
      )
    },
    {
      id: 'visibility',
      title: '02 // Visibility Computation',
      icon: BarChart2,
      color: 'from-purple-500/20 to-pink-500/10',
      accent: 'text-purple-400',
      content: (
        <div className="font-card">
          <div className="mb-5 flex items-baseline justify-between">
            <span className="text-sm text-gray-400">OVERALL SCORE</span>
            <span className="text-2xl font-bold text-purple-400">{visScore}{typeof visScore === 'number' ? '%' : ''}</span>
          </div>
          <div className="space-y-6">
            {visItems.map((item, index) => (
              <div key={index} className="space-y-2">
                <div className="flex justify-between items-baseline text-sm">
                  <span className="text-gray-300 uppercase text-base">{item.label}</span>
                  <span className="text-white font-bold text-xl">{item.value}%</span>
                </div>
                <div className="w-full h-3 bg-white/5 rounded-full overflow-hidden">
                  <div 
                    style={{ width: `${Math.min(item.value, 100)}%` }}
                    className="h-full bg-gradient-to-r from-purple-500 to-pink-500 rounded-full shadow-[0_0_15px_rgba(168,85,247,0.5)]"
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )
    },
    {
      id: 'locations',
      title: '03 // Optimal Locations',
      icon: MapPin,
      color: 'from-blue-500/20 to-cyan-500/10',
      accent: 'text-blue-400',
      content: (
        <div className="space-y-4 font-card">
          {(optimalLocation || bestSpotNearOptimal) && (
            <div className="space-y-3">
              {optimalLocation && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedMapLocation({
                      name: 'Calculated Optimal Coordinate',
                      latitude: optimalLocation.latitude,
                      longitude: optimalLocation.longitude,
                      score: optimalLocation.score,
                      distance_km: optimalLocation.distance_km,
                      bearing: optimalLocation.bearing,
                      best_spot_name: bestSpotNearOptimal?.name || null,
                      best_spot_score: bestSpotNearOptimal?.score ?? null,
                      best_spot_distance_km: bestSpotNearOptimal?.distance_from_optimal_km ?? null,
                      bortle_class: bestSpotNearOptimal?.bortle_class ?? null,
                      elevation: bestSpotNearOptimal?.elevation ?? null,
                      openness: bestSpotNearOptimal?.openness ?? null,
                    });
                    setMapMode('real_world');
                  }}
                  className="w-full text-left p-4 bg-cyan-500/10 border border-cyan-400/35 rounded-xl hover:bg-cyan-500/15 transition-all"
                >
                  <span className="font-bold text-cyan-200 text-lg block">
                    Calculated Optimal Coordinate
                  </span>
                  <span className="text-sm text-cyan-100/80 block mt-1">
                    {Number(optimalLocation.latitude).toFixed(5)}, {Number(optimalLocation.longitude).toFixed(5)}
                  </span>
                  <span className="text-xs text-cyan-100/70 block mt-1">
                    Score {optimalLocation.score ?? '—'} · {optimalLocation.distance_km ?? '—'} km · bearing {optimalLocation.bearing ?? '—'}°
                  </span>
                </button>
              )}

              {bestSpotNearOptimal && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedMapLocation(bestSpotNearOptimal);
                    setMapMode('real_world');
                  }}
                  className="w-full text-left p-4 bg-indigo-500/10 border border-indigo-400/30 rounded-xl hover:bg-indigo-500/15 transition-all"
                >
                  <span className="font-bold text-indigo-200 text-lg block">
                    Best Spot Near Optimal
                  </span>
                  <span className="text-sm text-indigo-100/85 block mt-1">
                    {bestSpotNearOptimal.name || 'Unnamed location'}
                  </span>
                  <span className="text-xs text-indigo-100/70 block mt-1">
                    Score {bestSpotNearOptimal.score ?? '—'} · {bestSpotNearOptimal.distance_km ?? '—'} km from current
                    {bestSpotNearOptimal.distance_from_optimal_km != null ? ` · ${bestSpotNearOptimal.distance_from_optimal_km} km from optimal` : ''}
                  </span>
                </button>
              )}
            </div>
          )}

          {locationsList.map((loc, index) => (
            <button 
              key={index} 
              onClick={() => {
                if (loc.latitude && loc.longitude) {
                  setSelectedMapLocation(loc);
                  setMapMode('real_world');
                }
              }}
              className="w-full text-left p-4 bg-white/5 border border-white/5 rounded-xl flex justify-between items-center hover:border-cyan-400/50 hover:bg-white/10 transition-all duration-300 cursor-pointer group"
            >
              <div>
                <span className="font-bold text-white text-xl block group-hover:text-cyan-300 transition-colors">{loc.name || loc.location_name || `Location ${index + 1}`}</span>
                <span className="text-base text-gray-400 block mt-1">
                  {loc.elevation ? `EL: ${loc.elevation}` : ''}
                  {loc.distance_km ? `${loc.distance_km.toFixed(0)}km away` : loc.distance || ''}
                  {loc.bortle_class ? ` // Bortle ${loc.bortle_class}` : ''}
                </span>
              </div>
              <div className="text-right">
                <span className="text-lg font-bold text-blue-400 block group-hover:drop-shadow-[0_0_8px_rgba(56,189,248,0.8)] transition-all">{loc.score ?? '—'}</span>
                <span className="text-sm text-gray-400 block uppercase tracking-wider">{loc.openness || ''}</span>
              </div>
            </button>
          ))}
        </div>
      )
    },
    {
      id: 'events',
      title: '04 // Celestial Events',
      icon: Calendar,
      color: 'from-pink-500/20 to-purple-500/10',
      accent: 'text-pink-400',
      content: (
        <div className="space-y-4 font-card text-base">
          {eventsList.map((evt, index) => (
            <div key={index} className="py-3 flex justify-between border-b border-white/5 last:border-0">
              <span className="text-gray-300 uppercase text-sm">{evt.event}</span>
              <span className="text-pink-300 font-bold text-base">{evt.value}</span>
            </div>
          ))}
        </div>
      )
    },
    {
      id: 'forecast',
      title: '05 // Observation Forecast',
      icon: Clock,
      color: 'from-indigo-500/20 to-blue-500/10',
      accent: 'text-indigo-400',
      content: (
        <div className="grid grid-cols-1 gap-4 font-card text-sm">
          {forecastItems.map((item, index) => (
            <div key={index} className="p-4 bg-white/5 border border-white/5 rounded-2xl">
              <span className="text-gray-400 block text-base uppercase tracking-wider">{item.label}</span>
              <span className="text-xl font-bold text-indigo-300 block mt-1">{item.value}</span>
            </div>
          ))}
        </div>
      )
    },
    {
      id: 'time-plan',
      title: '06 // Manual Plan Time (Calls /plan)',
      icon: Clock,
      color: 'from-cyan-500/20 to-indigo-500/10',
      accent: 'text-cyan-300',
      content: (
        <div className="space-y-4 font-card">
          <p className="text-sm text-slate-300">Set date/time and run a fresh plan for this location.</p>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="date"
              value={customDate}
              onChange={(e) => setCustomDate(e.target.value)}
              className="bg-slate-900/70 border border-white/15 rounded-lg px-3 py-2 text-sm text-white"
            />
            <input
              type="time"
              value={customTime}
              onChange={(e) => setCustomTime(e.target.value)}
              className="bg-slate-900/70 border border-white/15 rounded-lg px-3 py-2 text-sm text-white"
            />
          </div>
          <button
            type="button"
            onClick={runCustomPlan}
            disabled={planningCustom || !activeCoords}
            className="w-full bg-cyan-400 text-slate-950 font-bold py-3 rounded-xl disabled:opacity-60"
          >
            {planningCustom ? 'RUNNING /PLAN...' : 'RUN /PLAN FOR SELECTED DATE & TIME'}
          </button>
          <div className="p-4 bg-white/5 border border-white/10 rounded-xl">
            <p className="text-sm text-slate-300">Showing conditions for:</p>
            <p className="text-lg font-bold text-cyan-200 mt-1">{planData ? `${planData.date} ${planData.time}` : 'No plan loaded yet'}</p>
            <p className="text-sm text-slate-400 mt-2">Window: {planData?.best_window ?? '—'}</p>
          </div>
        </div>
      )
    },
    {
      id: 'future',
      title: '07 // Upcoming Days Forecast (Calls /future)',
      icon: Calendar,
      color: 'from-violet-500/20 to-blue-500/10',
      accent: 'text-violet-300',
      content: (
        <div className="space-y-3 font-card">
          <div className="p-4 bg-white/5 border border-white/10 rounded-xl">
            <p className="text-sm text-slate-300">Best upcoming night</p>
            <p className="text-xl font-bold text-violet-200 mt-1">{futureData ? `${futureData.best_date} @ ${futureData.best_time}` : 'Search a location to load'}</p>
            <p className="text-sm text-slate-400 mt-1">Score: {futureData?.best_score ?? '—'} · Window: {futureData?.best_window ?? '—'}</p>
          </div>
          {futureItems.map((day, idx) => (
            <div key={`${day.date}-${idx}`} className="p-3 bg-slate-900/45 border border-white/10 rounded-lg flex justify-between items-center">
              <div>
                <p className="text-sm font-semibold text-white">{day.date}</p>
                <p className="text-xs text-slate-400">{day.sky_quality} · Clouds {day.weather_summary?.cloud_cover ?? '—'}%</p>
              </div>
              <p className="text-lg font-bold text-violet-300">{day.score ?? '—'}</p>
            </div>
          ))}
        </div>
      )
    },
    {
      id: 'directions',
      title: '08 // Direction Guide (Aurora & Objects)',
      icon: Route,
      color: 'from-emerald-500/20 to-cyan-500/10',
      accent: 'text-emerald-300',
      content: (
        <div className="space-y-3 font-card">
          {directionItems.length === 0 && (
            <div className="p-4 bg-white/5 border border-white/10 rounded-xl text-sm text-slate-300">
              Search a location to load live directional guidance.
            </div>
          )}
          {directionItems.map((item) => (
            <div key={item.label} className="p-4 bg-white/5 border border-white/10 rounded-xl">
              <p className="text-base font-bold text-emerald-200">{item.label}</p>
              <p className="text-sm text-slate-300 mt-1">Altitude: {Number(item.altitude).toFixed(1)}° · Azimuth: {Number(item.azimuth).toFixed(1)}°</p>
              <p className="text-xs uppercase tracking-wider text-slate-500 mt-2">{item.source}</p>
            </div>
          ))}
        </div>
      )
    },
    {
      id: 'astronomy',
      title: '09 // Astronomy Objects (Calls /astronomy)',
      icon: Telescope,
      color: 'from-fuchsia-500/20 to-indigo-500/10',
      accent: 'text-fuchsia-300',
      content: (
        <div className="space-y-4 font-card">
          <p className="text-sm text-slate-300">Visible planets, stars, and constellations for selected date/time.</p>
          <div className="space-y-2">
            <p className="text-xs tracking-[0.25em] text-slate-400 uppercase">Planets</p>
            <div className="flex gap-2 overflow-x-auto slide-scroll pb-1">
              {(astronomyPlanets.length ? astronomyPlanets : [{ name: 'No planet data yet' }]).map((p, i) => (
                <div key={`pl-${i}`} className="min-w-[180px] p-3 bg-white/5 border border-white/10 rounded-xl">
                  <p className="font-semibold text-fuchsia-200">{p.name}</p>
                  <p className="text-xs text-slate-400 mt-1">Alt {p.altitude ?? '—'}° · Az {p.azimuth ?? '—'}°</p>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <p className="text-xs tracking-[0.25em] text-slate-400 uppercase">Stars</p>
            <div className="flex gap-2 overflow-x-auto slide-scroll pb-1">
              {(astronomyStars.slice(0, 12).length ? astronomyStars.slice(0, 12) : [{ name: 'No star data yet' }]).map((s, i) => (
                <div key={`st-${i}`} className="min-w-[180px] p-3 bg-white/5 border border-white/10 rounded-xl">
                  <p className="font-semibold text-cyan-200">{s.name}</p>
                  <p className="text-xs text-slate-400 mt-1">Alt {s.altitude ?? '—'}° · Az {s.azimuth ?? '—'}°</p>
                </div>
              ))}
            </div>
          </div>
          <div className="space-y-2">
            <p className="text-xs tracking-[0.25em] text-slate-400 uppercase">Constellations</p>
            <div className="flex gap-2 overflow-x-auto slide-scroll pb-1">
              {(astronomyConsts.slice(0, 10).length ? astronomyConsts.slice(0, 10) : [{ name: 'No constellation data yet' }]).map((c, i) => (
                <div key={`co-${i}`} className="min-w-[200px] p-3 bg-white/5 border border-white/10 rounded-xl">
                  <p className="font-semibold text-indigo-200">{c.name}</p>
                  <p className="text-xs text-slate-400 mt-1">Alt {c.altitude ?? '—'}° · Az {c.azimuth ?? '—'}°</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )
    },
    {
      id: 'camera',
      title: '10 // Best Camera Angles',
      icon: Camera,
      color: 'from-amber-500/20 to-orange-500/10',
      accent: 'text-amber-300',
      content: (
        <div className="space-y-3 font-card">
          <p className="text-sm text-slate-300">Recommended capture settings from current plan conditions.</p>
          {(cameraCards.length ? cameraCards : [{ key: 'NO CAMERA DATA', value: 'Run a search and /plan first' }]).map((item, idx) => (
            <div key={`${item.key}-${idx}`} className="p-4 bg-white/5 border border-white/10 rounded-xl flex justify-between items-center">
              <p className="text-xs tracking-[0.2em] text-slate-400">{item.key}</p>
              <p className="text-sm font-bold text-amber-200 text-right">{item.value}</p>
            </div>
          ))}
        </div>
      )
    },
  ];

  const primaryCardIds = ['conditions', 'visibility', 'locations', 'events', 'forecast'];
  const primaryCards = cards.filter((card) => primaryCardIds.includes(card.id));
  const featureCards = cards.filter((card) => !primaryCardIds.includes(card.id) && card.id !== 'directions');

  useEffect(() => {
    if (activeCard >= primaryCards.length) setActiveCard(0);
  }, [activeCard, primaryCards.length]);

  const nextCard = () => setActiveCard((prev) => (prev + 1) % primaryCards.length);
  const prevCard = () => setActiveCard((prev) => (prev - 1 + primaryCards.length) % primaryCards.length);

  /* --- floating element physics --- */
  useEffect(() => {
    let animationId;
    const items = floatingItemsRef.current;

    const updatePhysics = () => {
      if (!containerRef.current) return;

      const containerRect = containerRef.current.getBoundingClientRect();
      const containerW = containerRect.width;
      const containerH = containerRect.height;

      let cardRel = { left: containerW / 2 - 300, right: containerW / 2 + 300, top: containerH / 2 - 250, bottom: containerH / 2 + 250 };
      
      if (cardContainerRef.current) {
        const cardRect = cardContainerRef.current.getBoundingClientRect();
        cardRel.left = cardRect.left - containerRect.left;
        cardRel.top = cardRect.top - containerRect.top;
        cardRel.right = cardRel.left + cardRect.width;
        cardRel.bottom = cardRel.top + cardRect.height;
      }

      items.forEach((item, index) => {
        item.x += item.vx;
        item.y += item.vy;

        if (item.x < 0) { item.x = 0; item.vx = Math.abs(item.vx); }
        else if (item.x + item.w > containerW) { item.x = containerW - item.w; item.vx = -Math.abs(item.vx); }

        if (item.y < 0) { item.y = 0; item.vy = Math.abs(item.vy); }
        else if (item.y + item.h > containerH) { item.y = containerH - item.h; item.vy = -Math.abs(item.vy); }

        if (
          item.x + item.w > cardRel.left &&
          item.x < cardRel.right &&
          item.y + item.h > cardRel.top &&
          item.y < cardRel.bottom
        ) {
          const dLeft = (item.x + item.w) - cardRel.left;
          const dRight = cardRel.right - item.x;
          const dTop = (item.y + item.h) - cardRel.top;
          const dBottom = cardRel.bottom - item.y;
          const minD = Math.min(dLeft, dRight, dTop, dBottom);

          if (minD === dLeft) { item.vx = -Math.abs(item.vx); item.x = cardRel.left - item.w; }
          else if (minD === dRight) { item.vx = Math.abs(item.vx); item.x = cardRel.right; }
          else if (minD === dTop) { item.vy = -Math.abs(item.vy); item.y = cardRel.top - item.h; }
          else if (minD === dBottom) { item.vy = Math.abs(item.vy); item.y = cardRel.bottom; }
        }

        if (imageRefs.current[index]) {
          imageRefs.current[index].style.transform = `translate(${item.x}px, ${item.y}px)`;
        }
      });

      for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
          const itA = items[i];
          const itB = items[j];
          const cAx = itA.x + itA.w / 2;
          const cAy = itA.y + itA.h / 2;
          const cBx = itB.x + itB.w / 2;
          const cBy = itB.y + itB.h / 2;
          const dist = Math.hypot(cAx - cBx, cAy - cBy);
          const minSafety = (itA.w + itB.w) / 2;

          if (dist < minSafety) {
            const vxSwap = itA.vx; itA.vx = itB.vx; itB.vx = vxSwap;
            const vySwap = itA.vy; itA.vy = itB.vy; itB.vy = vySwap;
            const overlap = minSafety - dist;
            const pushX = ((cAx - cBx) / dist) * overlap / 2;
            const pushY = ((cAy - cBy) / dist) * overlap / 2;
            itA.x += pushX; itA.y += pushY;
            itB.x -= pushX; itB.y -= pushY;
          }
        }
      }

      animationId = requestAnimationFrame(updatePhysics);
    };

    animationId = requestAnimationFrame(updatePhysics);
    return () => cancelAnimationFrame(animationId);
  }, []);

  return (
    <div className="relative z-10 w-full font-sans text-white selection:bg-white/20 selection:text-white">
      
      {/* 1. Top Navigation */}
      <nav className="fixed top-0 left-0 w-full p-4 flex justify-between items-center bg-slate-950/80 backdrop-blur-md border-b border-white/10 z-50 text-white">
        <div className="flex items-end gap-3">
          <div className="w-2 h-2 rounded-full bg-white animate-pulse mt-1" />
          <div className="flex flex-col items-start gap-0">
            <span className="site-title beau-rivage-regular text-xl sm:text-2xl lg:text-3xl font-bold tracking-[0.18em] pb-1 border-b border-white/20 leading-none">NightOwl</span>
            <span className="font-mono text-[10px] uppercase tracking-[0.35em] text-slate-400">SYS</span>
          </div>
        </div>
        <div className="hidden md:flex gap-6 font-mono text-base tracking-widest text-slate-300">
          {primaryCards.map((card, index) => (
            <button
              key={card.id}
              onClick={() => handleNavClick(index)}
              className={`hover:text-white transition-colors uppercase ${activeCard === index ? 'text-white font-bold border-b border-white' : ''}`}
            >
              {card.id}
            </button>
          ))}
        </div>
        <div className="hidden sm:block w-[160px]" />
      </nav>

      {/* Main Content Flow */}
      <div className="flex flex-col items-center w-full bg-transparent">
        
        {/* 2. Hero Section */}
        <section className="min-h-[90vh] flex flex-col items-center justify-center max-w-xl w-full px-4 text-white">
          <motion.div 
            {...fadeInUp}
            className="w-full p-0"
          >
            <h1 className="text-lg sm:text-xl lg:text-2xl font-mono font-bold tracking-[0.22em] text-center text-white mb-6 uppercase">
              Locate Optimal Observation Zone
            </h1>
            <form onSubmit={handleSearch} className="relative flex items-center">
              <input 
                ref={inputRef}
                type="text"
                value={searchQuery}
                onChange={handleInputChange}
                onFocus={() => predictions.length > 0 && setShowDropdown(true)}
                onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
                placeholder="ENTER COORDINATES OR LOCATION..."
                className="w-full bg-slate-950/60 backdrop-blur-md border border-white/20 rounded-lg py-4 pl-12 pr-32 text-base font-mono tracking-wider text-white placeholder-slate-400 focus:outline-none focus:border-white/40 shadow-sm transition-all"
                autoComplete="off"
              />
              <Search className="absolute left-4 text-slate-400 w-5 h-5" />
              <button 
                type="submit"
                disabled={isSearching}
                className="absolute right-2 top-2 bottom-2 bg-white text-slate-950 hover:bg-slate-200 disabled:bg-slate-400 font-mono text-base font-bold tracking-wider px-4 rounded transition-all flex items-center gap-1 uppercase"
              >
                {isSearching ? <><Loader2 className="w-4 h-4 animate-spin" /> LOADING</> : 'SEARCH'}
              </button>

              {/* Google Places Autocomplete Dropdown */}
              {showDropdown && predictions.length > 0 && (
                <div className="absolute top-full mt-2 left-0 w-full bg-slate-950/95 backdrop-blur-xl border border-white/10 rounded-lg overflow-hidden z-50 shadow-2xl">
                  {predictions.map((p) => (
                    <button
                      key={p.place_id}
                      type="button"
                      onMouseDown={() => selectPrediction(p)}
                      className="w-full text-left px-4 py-3 text-sm font-mono text-gray-300 hover:bg-white/10 hover:text-white transition-colors border-b border-white/5 last:border-0 flex items-center gap-3"
                    >
                      <MapPin className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                      <span className="truncate">{p.description}</span>
                    </button>
                  ))}
                </div>
              )}
            </form>

            {error && <p className="mt-4 text-center text-red-400 font-mono text-xs">{error}</p>}
            {locationLabel && !isSearching && (
              <p className="mt-4 text-center text-gray-400 font-mono text-xs truncate">
                Showing results for: <span className="text-white">{locationLabel}</span>
              </p>
            )}
          </motion.div>
        </section>

        {/* 3. Spacer Section */}
        <section className="min-h-[60vh] w-full flex items-center justify-center relative pointer-events-none">
          <span className="text-slate-400 font-mono text-[10px] tracking-[0.3em] uppercase animate-pulse">
            SCROLL TO INITIATE SPACE LEAKAGE
          </span>
        </section>

        {/* 4. Leakage Visual & Constellation View */}
        <section 
          ref={containerRef}
          id="constellation" 
          className="min-h-screen w-full bg-[#0a1128]/95 backdrop-blur-sm text-white relative pt-24 pb-28 px-6 flex flex-col items-center justify-end gap-10 overflow-hidden border-t border-white/10"
        >
          {/* Wave SVG */}
          <div className="absolute top-0 left-0 w-full overflow-hidden leading-[0] z-10 pointer-events-none">
            <svg viewBox="0 0 1200 120" preserveAspectRatio="none" className="relative block w-full h-[150px] transform rotate-180 opacity-90">
              <path d="M0,0 C300,130 900,130 1200,0 L1200,120 L0,120 Z" fill="#000000" className="mix-blend-multiply opacity-20"></path>
            </svg>
            <div className="absolute top-[80px] left-1/2 -translate-x-1/2 w-48 h-48 bg-cyan-400/20 rounded-full blur-3xl animate-pulse" />
          </div>

          <div className="absolute inset-0 pointer-events-none z-0">
            <div className="absolute top-1/3 left-1/4 w-[500px] h-[500px] bg-cyan-500/10 rounded-full blur-[160px] animate-pulse" />
            <div className="absolute bottom-1/3 right-1/4 w-[500px] h-[500px] bg-indigo-500/10 rounded-full blur-[160px] animate-pulse" style={{ animationDelay: '3s' }} />
          </div>

          {floatingItemsRef.current.map((item, index) => (
            <img 
              key={index}
              ref={(el) => (imageRefs.current[index] = el)}
              src={item.src}
              alt="Floating Element"
              style={{ width: `${item.w}px`, height: `${item.h}px` }}
              className="absolute left-0 top-0 object-contain pointer-events-none mix-blend-screen opacity-90 filter drop-shadow-[0_0_25px_rgba(255,255,255,0.2)] z-10"
              onError={(e) => { e.target.style.display = 'none'; }}
            />
          ))}

          <div className="relative z-20 text-center max-w-2xl w-full">
            <span className="font-mono text-sm text-cyan-400 tracking-[0.3em] uppercase animate-pulse">
              DEEP SPACE TELEMETRY
            </span>
            <h2 className="text-2xl font-mono font-bold tracking-wider text-white mt-2 uppercase">
              ORBITAL STACK
            </h2>
          </div>

          {/* Card Stack Carousel */}
          <div 
            ref={cardContainerRef}
            className="relative z-20 w-full max-w-2xl h-[680px] flex items-center justify-center"
          >
            <button 
              onClick={prevCard}
              className="absolute left-[-60px] z-30 p-3 bg-white/5 hover:bg-white/10 border border-white/10 rounded-full text-white transition-all hidden md:block"
            >
              <ChevronLeft className="w-6 h-6" />
            </button>

            <div className="relative w-full h-full flex items-center justify-center">
              <AnimatePresence mode="popLayout">
                {primaryCards.map((card, index) => {
                  if (index !== activeCard) return null;

                  return (
                    <motion.div
                      key={card.id}
                      initial={{ opacity: 0, scale: 0.8, x: 100 }}
                      animate={{ opacity: 1, scale: 1, x: 0 }}
                      exit={{ opacity: 0, scale: 0.8, x: -100 }}
                      transition={{ type: "spring", stiffness: 300, damping: 30 }}
                      className="absolute w-full h-full bg-[#08111f]/95 backdrop-blur-3xl border border-white/10 rounded-[1.75rem] p-6 shadow-[0_30px_90px_rgba(0,165,255,0.18)] overflow-hidden flex flex-col font-card"
                    >
                      <div className="pointer-events-none absolute inset-0 rounded-[2rem] border border-white/5" />
                      <div className="pointer-events-none absolute top-6 left-6 w-22 h-1 rounded-full bg-cyan-400/20 blur-sm" />
                      <div className="pointer-events-none absolute bottom-6 right-6 w-24 h-1 rounded-full bg-purple-400/20 blur-sm" />
                      <div className="pointer-events-none absolute top-5 right-5 rounded-full border border-cyan-300/20 bg-slate-900/60 px-2 py-0.5 text-[10px] uppercase tracking-[0.3em] text-cyan-300">
                        CORE NODE
                      </div>
                      <div>
                        <div className="relative flex items-center gap-3 pb-2 mb-4">
                          <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-cyan-400/30 via-transparent to-purple-400/30" />
                          <card.icon className={`relative z-10 w-6 h-6 ${card.accent}`} />
                          <h3 className="relative z-10 text-xl font-card font-bold tracking-[0.22em] text-white uppercase">
                            {card.title}
                          </h3>
                        </div>
                      </div>
                      <div className="flex-1 overflow-y-auto slide-scroll pr-2 space-y-4 min-h-0">
                        {card.content}
                      </div>

                      <div className="flex justify-center gap-2 mt-4 pb-2">
                        {primaryCards.map((_, dotIndex) => (
                          <div 
                            key={dotIndex} 
                            className={`w-2 h-2 rounded-full transition-all duration-300 ${dotIndex === activeCard ? 'bg-white w-6' : 'bg-white/20'}`}
                          />
                        ))}
                      </div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </div>

            <button 
              onClick={nextCard}
              className="absolute right-[-60px] z-30 p-3 bg-white/5 hover:bg-white/10 border border-white/10 rounded-full text-white transition-all hidden md:block"
            >
              <ChevronRight className="w-6 h-6" />
            </button>
          </div>

          <div className="flex gap-4 md:hidden relative z-20">
            <button onClick={prevCard} className="p-3 bg-white/5 border border-white/10 rounded-full text-white">
              <ChevronLeft className="w-6 h-6" />
            </button>
            <button onClick={nextCard} className="p-3 bg-white/5 border border-white/10 rounded-full text-white">
              <ChevronRight className="w-6 h-6" />
            </button>
          </div>

        </section>

        <section className="w-full max-w-6xl px-6 py-20">
          <div className="mb-8 text-center">
            <span className="font-mono text-xs tracking-[0.28em] uppercase text-cyan-300">Advanced Insights</span>
            <h3 className="mt-3 text-2xl font-bold tracking-[0.16em] uppercase text-white">Time Planning · Future · Astronomy · Camera</h3>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {featureCards.map((card) => (
              <div key={`feature-${card.id}`} className="bg-[#08111f]/95 border border-white/10 rounded-[1.5rem] p-5 shadow-[0_20px_60px_rgba(0,165,255,0.12)]">
                <div className="flex items-center gap-3 mb-4 pb-2 border-b border-white/10">
                  <card.icon className={`w-5 h-5 ${card.accent}`} />
                  <h4 className="text-sm font-bold tracking-[0.18em] uppercase text-white">{card.title}</h4>
                </div>
                <div className="space-y-3">
                  {card.content}
                </div>
              </div>
            ))}
          </div>
        </section>

      </div>

      <footer className="w-full text-center font-mono text-[10px] text-gray-500 py-6 border-t border-white/5 bg-slate-950 relative z-20">
        <span>&copy; 2026 NIGHTOWL OBSERVATORY // ALL SYSTEMS OPERATIONAL</span>
      </footer>

      <AnimatePresence>
        {mapMode === 'real_world' && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4 }}
            className="fixed inset-0 z-50 bg-[#03030b] overflow-hidden"
          >
            <LocationDetail
              location={selectedMapLocation}
              onClose={() => setMapMode('space')}
            />
          </motion.div>
        )}
      </AnimatePresence>

    </div>
  );
}
