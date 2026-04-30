import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Eye, Wind, Cloud, Sun, Moon, Compass, BarChart2, Shield, Activity } from 'lucide-react';

export default function NightOwl() {
  const [isLocating, setIsLocating] = useState(false);
  const [viewMode, setViewMode] = useState('dashboard'); // 'dashboard' or 'analysis'

  // Mock data
  const missionData = {
    visibilityScore: 88,
    aqi: 24,
    cloudCover: 12,
    lightPollution: 3, // Bortle Scale
    moonBrightness: 15,
  };

  const conditions = [
    { label: 'Air Quality (AQI)', value: '24', status: 'Optimal', icon: Wind },
    { label: 'Light Pollution', value: 'Class 3', status: 'Bortle Scale', icon: Eye },
    { label: 'Cloud Forecast', value: '12%', status: 'Clear', icon: Cloud },
    { label: 'Moon Illumination', value: '15%', status: 'Waxing Crescent', icon: Moon },
    { label: 'Atmospheric Clarity', value: '92%', status: 'Excellent', icon: Sun },
  ];

  const visibilityBreakdown = [
    { label: 'Stellar Visibility Probability', value: 94 },
    { label: 'Milky Way Visibility', value: 85 },
    { label: 'Aurora Probability', value: 12 },
  ];

  const handleLocateZone = () => {
    setIsLocating(true);
    setTimeout(() => {
      setIsLocating(false);
      alert('Optimal Observation Zone identified: Lat 43.4723° N, Long 80.5449° W (Bortle Class 2)');
    }, 2000);
  };

  return (
    <div className="min-h-screen text-gray-900 font-sans selection:bg-gray-200 selection:text-gray-900 p-6 md:p-12 flex flex-col justify-between pointer-events-none">
      
      {/* Header - NASA Style */}
      <header className="w-full flex flex-col md:flex-row justify-between items-start md:items-center gap-4 border-b border-gray-200 pb-4 pointer-events-auto bg-[#fcfcfc]/90 backdrop-blur-sm p-4 rounded-t-lg shadow-sm">
        <div className="flex items-center gap-3">
          <Activity className="text-gray-700 w-5 h-5" />
          <div>
            <h1 className="text-xl font-mono font-bold tracking-wider text-gray-800">NightOwl // SKY_OBS_SYS</h1>
            <p className="text-xs font-mono text-gray-500 uppercase tracking-widest mt-0.5">National Aerospace & Observation Command</p>
          </div>
        </div>
        <div className="flex gap-6 font-mono text-xs text-gray-600">
          <div>
            <span className="text-gray-400">STATUS:</span> <span className="text-green-600 font-bold">NOMINAL</span>
          </div>
          <div>
            <span className="text-gray-400">LOC:</span> 43.4723° N, 80.5449° W
          </div>
          <div>
            <span className="text-gray-400">SYS_TIME:</span> {new Date().toISOString().slice(11, 19)} UTC
          </div>
        </div>
      </header>

      {/* Main Content Grid */}
      <main className="grid grid-cols-1 lg:grid-cols-3 gap-6 my-6 pointer-events-auto">
        
        {/* Panel 2: Main Panel (Mission Overview) */}
        <section className="bg-[#fcfcfc] border border-gray-200 rounded-lg shadow-sm p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 border-b border-gray-100 pb-3 mb-4">
              <Compass className="text-gray-600 w-4 h-4" />
              <h2 className="text-xs font-mono font-bold tracking-widest text-gray-500 uppercase">01 / Mission Overview</h2>
            </div>
            
            <div className="flex items-baseline justify-between mb-6">
              <span className="text-sm font-medium text-gray-600">Sky Visibility Score</span>
              <span className="text-6xl font-mono font-bold tracking-tighter text-gray-800">{missionData.visibilityScore}</span>
            </div>

            <div className="space-y-3 font-mono text-xs border-t border-gray-100 pt-4">
              <div className="flex justify-between py-1">
                <span className="text-gray-500">AIR QUALITY (AQI)</span>
                <span className="text-gray-800 font-bold">{missionData.aqi}</span>
              </div>
              <div className="flex justify-between py-1">
                <span className="text-gray-500">CLOUD COVER</span>
                <span className="text-gray-800 font-bold">{missionData.cloudCover}%</span>
              </div>
              <div className="flex justify-between py-1">
                <span className="text-gray-500">LIGHT POLLUTION</span>
                <span className="text-gray-800 font-bold">Class {missionData.lightPollution}</span>
              </div>
              <div className="flex justify-between py-1">
                <span className="text-gray-500">MOON BRIGHTNESS</span>
                <span className="text-gray-800 font-bold">{missionData.moonBrightness}%</span>
              </div>
            </div>
          </div>

          <button 
            onClick={handleLocateZone}
            disabled={isLocating}
            className="w-full mt-6 bg-gray-900 text-[#fcfcfc] hover:bg-gray-800 disabled:bg-gray-400 font-mono text-xs font-bold tracking-widest py-3 px-4 rounded transition-all duration-200 flex justify-center items-center gap-2 uppercase shadow-sm"
          >
            {isLocating ? 'Computing Optimal Coordinates...' : 'Locate Optimal Observation Zone'}
          </button>
        </section>

        {/* Panel 3: Conditions Module */}
        <section className="bg-[#fcfcfc] border border-gray-200 rounded-lg shadow-sm p-6 flex flex-col justify-between lg:col-span-1">
          <div>
            <div className="flex items-center gap-2 border-b border-gray-100 pb-3 mb-4">
              <Activity className="text-gray-600 w-4 h-4" />
              <h2 className="text-xs font-mono font-bold tracking-widest text-gray-500 uppercase">02 / Atmospheric Conditions</h2>
            </div>

            <div className="divide-y divide-gray-100">
              {conditions.map((item, index) => (
                <div key={index} className="py-3 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <item.icon className="text-gray-400 w-4 h-4" />
                    <span className="text-xs font-medium text-gray-700">{item.label}</span>
                  </div>
                  <div className="text-right font-mono">
                    <span className="text-sm font-bold text-gray-900">{item.value}</span>
                    <span className="block text-[10px] text-gray-400 uppercase tracking-wider">{item.status}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="text-[10px] font-mono text-gray-400 text-center mt-4">
            DATA SOURCE: NOAA-GOES-R // REAL-TIME FEED
          </div>
        </section>

        {/* Panel 4: Visibility Computation Panel */}
        <section className="bg-[#fcfcfc] border border-gray-200 rounded-lg shadow-sm p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center gap-2 border-b border-gray-100 pb-3 mb-4">
              <BarChart2 className="text-gray-600 w-4 h-4" />
              <h2 className="text-xs font-mono font-bold tracking-widest text-gray-500 uppercase">03 / Visibility Computation</h2>
            </div>

            <div className="mb-6">
              <span className="text-xs font-mono text-gray-400 uppercase tracking-widest block mb-1">Overall Computation</span>
              <div className="flex items-baseline gap-2">
                <span className="text-4xl font-mono font-bold text-gray-800">{missionData.visibilityScore}</span>
                <span className="text-xs font-mono text-green-600 font-bold uppercase">/ OPTIMAL</span>
              </div>
            </div>

            <div className="space-y-5 mt-4">
              {visibilityBreakdown.map((item, index) => (
                <div key={index} className="space-y-1">
                  <div className="flex justify-between items-baseline font-mono text-xs">
                    <span className="text-gray-500 uppercase">{item.label}</span>
                    <span className="text-gray-800 font-bold">{item.value}%</span>
                  </div>
                  <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <motion.div 
                      initial={{ width: 0 }}
                      animate={{ width: `${item.value}%` }}
                      transition={{ duration: 1, delay: 0.2 }}
                      className="h-full bg-gray-700 rounded-full"
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="border-t border-gray-100 pt-4 mt-6 flex justify-between items-center font-mono text-[10px] text-gray-400">
            <span>ALGORITHM: V4.2.1</span>
            <span>CONFIDENCE: 94.8%</span>
          </div>
        </section>

      </main>

      {/* Footer - Minimalist */}
      <footer className="w-full text-center font-mono text-[10px] text-gray-400 mt-auto pt-4 border-t border-gray-200/50 pointer-events-auto bg-[#fcfcfc]/50 backdrop-blur-sm p-2 rounded-b-lg shadow-sm flex justify-between items-center px-4">
        <span>&copy; 2026 NIGHTOWL OBSERVATORY</span>
        <span>ALL SYSTEMS OPERATIONAL // SECURE_LINK_STABLE</span>
      </footer>

    </div>
  );
}
