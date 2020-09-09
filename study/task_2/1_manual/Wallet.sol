pragma solidity ^0.6.4;

contract Proxy {

    // TODO: implement the Proxy contract
}

contract Wallet {
 address owner;

 mapping(address => uint256) balances;

 constructor() public {
     owner = msg.sender;
 }

 function deposit() public payable {
     assert(balances[msg.sender] + msg.value > balances[msg.sender]);
     balances[msg.sender] += msg.value;
 }

 function withdraw(uint256 amount) public {
     require(amount <= balances[msg.sender]);
     msg.sender.transfer(amount);
     balances[msg.sender] -= amount;
 }

 // In an emergency the owner can migrate  allfunds to a different address.
 function migrateTo(address payable to) public {
     // ACCESS_CONTROL missing
     to.transfer(address(this).balance);
 }
}
