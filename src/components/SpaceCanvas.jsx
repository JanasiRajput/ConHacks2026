import { Canvas } from '@react-three/fiber';
import { Suspense } from 'react';
import { EffectComposer, Bloom, Vignette } from '@react-three/postprocessing';
import { Cloud } from '@react-three/drei';
import SpaceStars from './Stars';
import Moon from './Moon';

export default function SpaceCanvas() {
  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      width: '100vw',
      height: '100vh',
      zIndex: -1,
      background: 'radial-gradient(circle at center, #0c0d21 0%, #03030b 100%)',
      pointerEvents: 'none',
    }}>
      <Canvas camera={{ position: [0, 0, 5], fov: 60 }}>
        <Suspense fallback={null}>
          <ambientLight intensity={0.3} />
          <directionalLight 
            position={[10, 10, 10]} 
            intensity={2} 
            color="#ffffff"
          />
          <directionalLight 
            position={[-10, -10, -10]} 
            intensity={0.5} 
            color="#a100ff" // Purple backlight
          />
          
          {/* Nebula/Cosmic Fog */}
          <group position={[0, 0, -20]}>
            <Cloud 
              texture="/earth_clouds.jpg"
              opacity={0.3} 
              speed={0.2} 
              width={20} 
              depth={5} 
              segments={10} 
              color="#00f3ff" // Cyan nebula
              position={[-10, 5, -5]}
            />
            <Cloud 
              texture="/earth_clouds.jpg"
              opacity={0.2} 
              speed={0.1} 
              width={25} 
              depth={5} 
              segments={10} 
              color="#a100ff" // Purple nebula
              position={[10, -5, -5]}
            />
          </group>

          <SpaceStars />
          <Moon />

          {/* Post-Processing for cinematic glow */}
          <EffectComposer>
            <Bloom 
              intensity={1.0} 
              luminanceThreshold={0.8} 
              luminanceSmoothing={0.1} 
              height={300} 
            />
            <Vignette eskil={false} offset={0.4} darkness={0.7} />
          </EffectComposer>
        </Suspense>
      </Canvas>
    </div>
  );
}
