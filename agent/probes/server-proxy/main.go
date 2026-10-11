// Fixed TCP ingress for FarmBot's internal service networks. No shell, files or
// general forwarding API. Four destinations are supplied by the owned controller.
package main

import (
	"io"
	"net"
	"os"
	"strings"
	"sync"
	"time"
)

func main() {
	if len(os.Args) != 5 {
		os.Exit(2)
	}
	permits := make(chan struct{}, 256)
	var listeners []net.Listener
	for _, pair := range os.Args[1:] {
		parts := strings.Split(pair, "=")
		if len(parts) != 2 {
			os.Exit(2)
		}
		listener, err := net.Listen("tcp4", "0.0.0.0:"+parts[0])
		if err != nil {
			os.Exit(1)
		}
		listeners = append(listeners, listener)
		destination := parts[1]
		go func() {
			for {
				client, err := listener.Accept()
				if err != nil {
					return
				}
				select {
				case permits <- struct{}{}:
					go relay(client, destination, permits)
				default:
					client.Close()
				}
			}
		}()
	}
	select {}
}

func relay(client net.Conn, destination string, permits chan struct{}) {
	defer func() { <-permits }()
	defer client.Close()
	server, err := net.DialTimeout("tcp4", destination, 5*time.Second)
	if err != nil {
		return
	}
	defer server.Close()
	// Even a wedged peer cannot retain relay memory past the maximum server lease.
	deadline := time.Now().Add(2 * time.Hour)
	client.SetDeadline(deadline)
	server.SetDeadline(deadline)
	var copies sync.WaitGroup
	copies.Add(2)
	go func() {
		defer copies.Done()
		io.Copy(server, client)
		if tcp, ok := server.(*net.TCPConn); ok {
			tcp.CloseWrite()
		}
	}()
	go func() {
		defer copies.Done()
		io.Copy(client, server)
		if tcp, ok := client.(*net.TCPConn); ok {
			tcp.CloseWrite()
		}
	}()
	copies.Wait()
}
