package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"log"
	"io/fs"
	"math/big"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const payloadDir = "../../payloads"

func loadPayloads() (map[string][]byte, []string, error) {
	entries, err := os.ReadDir(payloadDir)
	if err != nil {
		return nil, nil, err
	}

	payloads := make(map[string][]byte)
	routes := make([]string, 0)

	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		route, filePath, ok := payloadRoute(entry)
		if !ok {
			continue
		}

		payload, err := os.ReadFile(filePath)
		if err != nil {
			return nil, nil, err
		}

		payloads[route] = payload
		routes = append(routes, route)
	}

	if _, ok := payloads["/payloads/1kb"]; !ok {
		return nil, nil, fmt.Errorf("required payload file not found: %s", filepath.Join(payloadDir, "payload_1kb.json"))
	}

	payloads["/"] = payloads["/payloads/1kb"]
	routes = append(routes, "/")
	sort.Strings(routes)
	return payloads, routes, nil
}

func payloadRoute(entry fs.DirEntry) (string, string, bool) {
	name := entry.Name()
	if !strings.HasPrefix(name, "payload_") || !strings.HasSuffix(name, "kb.json") {
		return "", "", false
	}

	sizePart := strings.TrimSuffix(strings.TrimPrefix(name, "payload_"), ".json")
	return "/payloads/" + sizePart, filepath.Join(payloadDir, name), true
}

func generateCertificate() (tls.Certificate, error) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return tls.Certificate{}, err
	}

	serialNumberLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serialNumber, err := rand.Int(rand.Reader, serialNumberLimit)
	if err != nil {
		return tls.Certificate{}, err
	}

	template := x509.Certificate{
		SerialNumber: serialNumber,
		Subject: pkix.Name{
			CommonName: "localhost",
		},
		NotBefore: time.Now(),
		NotAfter:  time.Now().Add(24 * time.Hour),
		KeyUsage:  x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage: []x509.ExtKeyUsage{
			x509.ExtKeyUsageServerAuth,
		},
		DNSNames: []string{"localhost"},
	}

	certificateDER, err := x509.CreateCertificate(rand.Reader, &template, &template, &privateKey.PublicKey, privateKey)
	if err != nil {
		return tls.Certificate{}, err
	}

	certificatePEM := pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: certificateDER,
	})

	privateKeyPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "RSA PRIVATE KEY",
		Bytes: x509.MarshalPKCS1PrivateKey(privateKey),
	})

	return tls.X509KeyPair(certificatePEM, privateKeyPEM)
}

func main() {
	payloads, routes, err := loadPayloads()
	if err != nil {
		log.Fatal(err)
	}

	certificate, err := generateCertificate()
	if err != nil {
		log.Fatal(err)
	}

	server := &http.Server{
		Addr: ":8443",
		TLSConfig: &tls.Config{
			Certificates: []tls.Certificate{certificate},
		},
	}

	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if _, err := w.Write(payloads["/"]); err != nil {
			log.Println("write response error:", err)
		}
	})
	for route, payload := range payloads {
		if route == "/" {
			continue
		}
		responseBody := payload
		http.HandleFunc(route, func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			if _, err := w.Write(responseBody); err != nil {
				log.Println("write response error:", err)
			}
		})
	}

	fmt.Println("Server running at https://localhost:8443")
	fmt.Printf("Available endpoints: %s\n", strings.Join(routes, ", "))
	if err := server.ListenAndServeTLS("", ""); err != nil {
		log.Println("server error:", err)
	}
}
