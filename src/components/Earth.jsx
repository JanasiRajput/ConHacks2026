import { useRef, useMemo, useEffect } from 'react';
import { useFrame, useLoader } from '@react-three/fiber';
import { TextureLoader } from 'three';
import * as THREE from 'three';

// Custom shader for Day/Night Earth
const EarthShader = {
  vertexShader: `
    varying vec2 vUv;
    varying vec3 vNormal;
    varying vec3 vPosition;

    void main() {
      vUv = uv;
      vNormal = normalize(normalMatrix * normal);
      vPosition = (modelViewMatrix * vec4(position, 1.0)).xyz;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform sampler2D uDayTexture;
    uniform sampler2D uNightTexture;
    uniform vec3 uSunDirection;
    
    varying vec2 vUv;
    varying vec3 vNormal;
    varying vec3 vPosition;

    void main() {
      vec2 uv = vec2(1.0 - vUv.x, vUv.y);
      vec4 dayColor = texture2D(uDayTexture, uv);
      vec4 nightColor = texture2D(uNightTexture, uv);
      
      float intensity = dot(vNormal, normalize(uSunDirection));
      float mixAmount = smoothstep(-0.2, 0.2, intensity);
      vec4 finalColor = mix(nightColor, dayColor, mixAmount);
      
      // Mute the colors by blending with a dark, deep space blue/teal wash
      vec3 washColor = vec3(0.5, 0.6, 0.8);
      vec3 mutedColor = finalColor.rgb * washColor;
      
      float fresnel = dot(vNormal, vec3(0.0, 0.0, 1.0));
      fresnel = 1.0 - clamp(fresnel, 0.0, 1.0);
      vec3 glow = vec3(0.1, 0.3, 0.8) * pow(max(0.0, fresnel), 3.5); // Protected pow base
      
      gl_FragColor = vec4(mutedColor + glow, 1.0);
    }
  `
};

const AtmosphereShader = {
  vertexShader: `
    varying vec3 vNormal;
    void main() {
      vNormal = normalize(normalMatrix * normal);
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    varying vec3 vNormal;
    void main() {
      float intensity = pow(max(0.0, 0.6 - dot(vNormal, vec3(0, 0, 1.0))), 2.5); // Protected pow base
      gl_FragColor = vec4(0.2, 0.4, 0.8, 1.0) * intensity * 1.5; 
    }
  `
};

export default function Earth(props) {
  const earthRef = useRef();
  const cloudsRef = useRef();
  const scrollProgress = useRef(0);
  const targetScrollProgress = useRef(0);

  const [dayMap, nightMap, cloudsMap] = useLoader(TextureLoader, [
    '/earth_day.jpg',
    '/earth_night.jpg',
    '/earth_clouds.jpg'
  ]);

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

  const uniforms = useMemo(() => ({
    uDayTexture: { value: dayMap },
    uNightTexture: { value: nightMap },
    uSunDirection: { value: new THREE.Vector3(3, 3, 10) }
  }), [dayMap, nightMap]);

  useFrame((state, delta) => {
    if (!earthRef.current) return;

    // Smoothly interpolate scroll progress (lerp)
    scrollProgress.current += (targetScrollProgress.current - scrollProgress.current) * 0.1;

    // Base rotation
    earthRef.current.rotation.y += delta * 0.03;
    if (cloudsRef.current) {
      cloudsRef.current.rotation.y += delta * 0.04;
    }

    // Scroll animations
    // 1. Move away (Z position goes from 0 to -25)
    const targetZ = -scrollProgress.current * 25;
    earthRef.current.position.z = THREE.MathUtils.lerp(earthRef.current.position.z, targetZ, 0.1);

    // 2. Scale down (Scale goes from 3.5 to 1.0)
    const baseScale = 3.5;
    const targetScale = baseScale * (1 - scrollProgress.current * 0.7);
    earthRef.current.scale.setScalar(THREE.MathUtils.lerp(earthRef.current.scale.x, targetScale, 0.1));

    // 3. Move up/center as it goes back (Y goes from -2.5 to 0)
    const targetY = -2.5 + scrollProgress.current * 2.5;
    const targetX = scrollProgress.current * 2;
    earthRef.current.position.x = THREE.MathUtils.lerp(earthRef.current.position.x, targetX, 0.1);
    earthRef.current.position.y = THREE.MathUtils.lerp(earthRef.current.position.y, targetY, 0.1);
  });

  return (
    <group ref={earthRef} position={[0, -2.5, 0]} {...props}>
      {/* Earth Surface */}
      <mesh>
        <sphereGeometry args={[1, 64, 64]} />
        <shaderMaterial
          vertexShader={EarthShader.vertexShader}
          fragmentShader={EarthShader.fragmentShader}
          uniforms={uniforms}
        />
      </mesh>

      {/* Clouds Layer */}
      <mesh ref={cloudsRef} scale={[1.02, 1.02, 1.02]}>
        <sphereGeometry args={[1, 64, 64]} />
        <meshStandardMaterial
          map={cloudsMap}
          transparent={true}
          opacity={0.2}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>

      {/* Atmosphere Glow */}
      <mesh scale={[1.15, 1.15, 1.15]}>
        <sphereGeometry args={[1, 32, 32]} />
        <shaderMaterial
          vertexShader={AtmosphereShader.vertexShader}
          fragmentShader={AtmosphereShader.fragmentShader}
          blending={THREE.AdditiveBlending}
          side={THREE.BackSide}
          transparent={true}
        />
      </mesh>
    </group>
  );
}
