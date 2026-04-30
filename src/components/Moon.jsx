import { useRef, useEffect } from 'react';
import { useFrame, useLoader } from '@react-three/fiber';
import { TextureLoader } from 'three';
import * as THREE from 'three';

export default function Moon() {
  const moonRef = useRef();
  const venusRef = useRef();
  const marsRef = useRef();
  const scrollProgress = useRef(0);
  const targetScrollProgress = useRef(0);

  // Load textures
  const colorMap = useLoader(TextureLoader, '/earth_clouds.jpg');
  const venusMap = useLoader(TextureLoader, '/venus_texture.png');
  const marsMap = useLoader(TextureLoader, '/mars_texture.png');
  
  useEffect(() => {
    const handleScroll = () => {
      const totalScroll = document.documentElement.scrollHeight - window.innerHeight;
      if (totalScroll > 0) {
        targetScrollProgress.current = window.scrollY / totalScroll;
      }
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useFrame((state, delta) => {
    if (!moonRef.current) return;

    // Smoothly interpolate scroll progress (lerp)
    scrollProgress.current += (targetScrollProgress.current - scrollProgress.current) * 0.1;

    // 1. Moon Animation
    moonRef.current.rotation.y += delta * 0.05;
    const targetZ = -scrollProgress.current * 25;
    moonRef.current.position.z = THREE.MathUtils.lerp(moonRef.current.position.z, targetZ, 0.1);
    
    const baseScale = 3.5;
    const targetScale = baseScale * (1 - scrollProgress.current * 0.7);
    moonRef.current.scale.setScalar(THREE.MathUtils.lerp(moonRef.current.scale.x, targetScale, 0.1));

    const targetY = -2.5 + scrollProgress.current * 2.5;
    const targetX = scrollProgress.current * 2;
    moonRef.current.position.x = THREE.MathUtils.lerp(moonRef.current.position.x, targetX, 0.1);
    moonRef.current.position.y = THREE.MathUtils.lerp(moonRef.current.position.y, targetY, 0.1);

    // 2. Venus Animation (Left)
    if (venusRef.current) {
      venusRef.current.rotation.y -= delta * 0.03;
      const venusX = -7.5 - scrollProgress.current * 15;
      const venusY = -1.5 + Math.sin(scrollProgress.current * Math.PI) * 4;
      const venusZ = -2 - scrollProgress.current * 15;
      const venusScale = 1.2 * (1 - scrollProgress.current * 0.8);
      
      venusRef.current.position.set(venusX, venusY, venusZ);
      venusRef.current.scale.setScalar(venusScale);
      venusRef.current.material.opacity = Math.max(0, 1 - scrollProgress.current * 1.5);
    }

    // 3. Mars Animation (Right)
    if (marsRef.current) {
      marsRef.current.rotation.y += delta * 0.02;
      const marsX = 7.5 + scrollProgress.current * 15;
      const marsY = -1.5 + Math.sin(scrollProgress.current * Math.PI) * 4;
      const marsZ = -2 - scrollProgress.current * 15;
      const marsScale = 0.9 * (1 - scrollProgress.current * 0.8);
      
      marsRef.current.position.set(marsX, marsY, marsZ);
      marsRef.current.scale.setScalar(marsScale);
      marsRef.current.material.opacity = Math.max(0, 1 - scrollProgress.current * 1.5);
    }
  });

  return (
    <group>
      {/* Main Moon Sphere */}
      <mesh ref={moonRef} position={[0, -2.5, 0]}>
        <sphereGeometry args={[1, 64, 64]} />
        <meshStandardMaterial 
          map={colorMap}
          roughness={0.6}
          metalness={0.1}
          transparent={true}
          opacity={0.9}
        />
      </mesh>

      {/* Venus Sphere (Left) */}
      <mesh ref={venusRef} position={[-4.5, -1.5, -2]}>
        <sphereGeometry args={[1, 32, 32]} />
        <meshStandardMaterial 
          map={venusMap}
          roughness={0.4}
          metalness={0.1}
          transparent={true}
          opacity={1.0}
        />
      </mesh>

      {/* Mars Sphere (Right) */}
      <mesh ref={marsRef} position={[4.5, -1.5, -2]}>
        <sphereGeometry args={[1, 32, 32]} />
        <meshStandardMaterial 
          map={marsMap}
          roughness={0.8}
          metalness={0.1}
          transparent={true}
          opacity={1.0}
        />
      </mesh>
    </group>
  );
}
